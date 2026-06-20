"""Model router — picks local fast vs cloud frontier vs vision per step (§6.3).

Dual-loop architecture:
  * **Fast loop** (local_fast): reflexive tool picks when AX is clear; tens–hundreds of ms.
  * **Slow loop** (cloud_frontier): planning, novel goals, recovery after failures.
  * **Vision path** (vision): when AX tree is empty or element lookup fails.

Both loops share the WorldModel blackboard; the orchestrator calls `route()` before
each LLM step and `pick_client()` to obtain the right backend.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

from .llm import LLM, LLMBackend, LocalHTTPClient, VisionLLM
from .llm_errors import FailoverLLMClient
from .providers import collect_api_keys, create_client, merge_role_config

if TYPE_CHECKING:
    from .world_model import WorldModel

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]


class RouteTier(str, Enum):
    LOCAL_FAST = "local_fast"
    CLOUD_FRONTIER = "cloud_frontier"
    VISION = "vision"


@dataclass
class RouteDecision:
    tier: RouteTier
    reason: str
    use_vision_context: bool = False


@dataclass
class RouterConfig:
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "RouterConfig":
        p = Path(path) if path else ROOT / "configs" / "router.yaml"
        raw: dict[str, Any] = {}
        if p.exists():
            raw = yaml.safe_load(p.read_text()) or {}
        return cls(raw=raw)

    def role_config(self, role: str) -> dict[str, Any]:
        return merge_role_config(self.raw, role)

    @property
    def routing(self) -> dict[str, Any]:
        return dict(self.raw.get("routing") or {})


class Router:
    """Classify each agent step and provide the matching LLM client."""

    def __init__(
        self,
        router_cfg: RouterConfig | None = None,
        cloud_llm: LLMBackend | None = None,
        anthropic_api_key: str | None = None,
        api_keys: dict[str, str | None] | None = None,
    ):
        self.cfg = router_cfg or RouterConfig.load()
        self._cloud = cloud_llm
        self._anthropic_key = anthropic_api_key
        self._api_keys = api_keys or collect_api_keys()
        self._clients: dict[str, Any] = {}
        self._local_available: bool | None = None
        self._last_decision: RouteDecision | None = None

    @property
    def last_decision(self) -> RouteDecision | None:
        return self._last_decision

    def set_cloud_llm(self, llm: LLM) -> None:
        self._cloud = llm

    # ---- routing heuristics ----

    def route(
        self,
        world: "WorldModel",
        *,
        careful: bool = False,
        force_tier: RouteTier | None = None,
        ax_miss: bool = False,
        force_local: bool = False,
    ) -> RouteDecision:
        """Classify the next reasoning step."""
        if force_tier is not None:
            decision = RouteDecision(force_tier, f"forced:{force_tier.value}")
            self._last_decision = decision
            return decision

        if force_local and self._local_available_check():
            decision = RouteDecision(RouteTier.LOCAL_FAST, "local_only_mode")
            self._last_decision = decision
            return decision

        routing = self.cfg.routing
        failure_threshold = int(routing.get("failure_threshold_cloud", 2))
        ax_empty_threshold = int(routing.get("ax_empty_threshold", 3))

        # Hard reasoning / recovery → cloud
        if world.step_failure_count >= failure_threshold:
            decision = RouteDecision(
                RouteTier.CLOUD_FRONTIER,
                f"failures={world.step_failure_count}>={failure_threshold}",
            )
            self._last_decision = decision
            return decision

        # AX insufficient → vision
        if ax_miss or world.element_count == 0 or world.element_count < ax_empty_threshold:
            if world.ax_insufficient or ax_miss:
                decision = RouteDecision(
                    RouteTier.VISION,
                    f"ax_insufficient (elements={world.element_count})",
                    use_vision_context=True,
                )
                self._last_decision = decision
                return decision

        # Careful mode or novel goal → cloud for planning
        if careful or world.is_novel_goal:
            decision = RouteDecision(RouteTier.CLOUD_FRONTIER, "careful_or_novel")
            self._last_decision = decision
            return decision

        # Self-correction in progress → cloud
        if world.needs_replan:
            decision = RouteDecision(RouteTier.CLOUD_FRONTIER, "replan_after_verify_fail")
            self._last_decision = decision
            return decision

        # Reflexive path when AX looks good
        if world.element_count >= ax_empty_threshold and self._local_available_check():
            decision = RouteDecision(
                RouteTier.LOCAL_FAST,
                f"reflexive_ax_ok (elements={world.element_count})",
            )
            self._last_decision = decision
            return decision

        decision = RouteDecision(RouteTier.CLOUD_FRONTIER, "default_cloud")
        self._last_decision = decision
        return decision

    def is_reflexive_tool(self, tool_name: str) -> bool:
        reflexive = self.cfg.routing.get("reflexive_tools") or []
        return tool_name in reflexive

    # ---- client selection ----

    def _local_available_check(self) -> bool:
        if self._local_available is not None:
            return self._local_available
        role = self.cfg.role_config("local_fast")
        if role.get("backend") != "http":
            self._local_available = False
            return False
        client = self._get_or_create_local()
        self._local_available = client.available()
        if not self._local_available:
            log.info("Local fast model unavailable; falling back to cloud.")
        return self._local_available

    def pick(self, role: str) -> dict[str, Any]:
        """Return merged provider config for a role (vision, cloud_frontier, …)."""
        return self.cfg.role_config(role)

    def _tier_role(self, tier: RouteTier) -> str:
        if tier == RouteTier.LOCAL_FAST:
            return "local_fast"
        if tier == RouteTier.VISION:
            return "vision"
        return "cloud_frontier"

    def _provider_chain(self, role: str) -> list[str]:
        """Primary provider plus failover_providers for a role."""
        roles = self.cfg.raw.get("roles") or {}
        role_raw = dict(roles.get(role) or {})
        chain: list[str] = []
        primary = role_raw.get("provider")
        if primary:
            chain.append(str(primary))
        for name in role_raw.get("failover_providers") or []:
            s = str(name)
            if s and s not in chain:
                chain.append(s)
        return chain

    def _create_client_for_role(self, role: str, provider_name: str | None = None) -> Any | None:
        roles = self.cfg.raw.get("roles") or {}
        role_cfg = dict(roles.get(role) or {})
        if provider_name:
            role_cfg = dict(role_cfg)
            role_cfg["provider"] = provider_name
            providers = self.cfg.raw.get("providers") or {}
            base = dict(providers.get(provider_name) or {})
            base.update({k: v for k, v in role_cfg.items() if k != "provider"})
            role_cfg = base
        else:
            role_cfg = merge_role_config(self.cfg.raw, role)
        return create_client(role_cfg, api_keys=self._api_keys, role_name=role)

    def pick_client(self, decision: RouteDecision) -> Any:
        """Return an object with `.step(system, messages, tools) -> LLMResponse`."""
        return self.pick_client_with_failover(decision.tier)

    def pick_client_with_failover(self, tier: RouteTier) -> Any:
        """Return LLM client with provider failover chain (Phase 10, NFR-3)."""
        if tier == RouteTier.LOCAL_FAST and self._local_available_check():
            return self._get_or_create_local()
        if tier == RouteTier.VISION:
            return self._get_or_create_vision_with_failover()
        return self._get_cloud_with_failover()

    def _get_cloud(self) -> LLMBackend:
        return self._get_cloud_with_failover()

    def _get_cloud_with_failover(self) -> LLMBackend:
        cache_key = "cloud_frontier"
        if cache_key in self._clients:
            return self._clients[cache_key]
        chain = self._provider_chain("cloud_frontier")
        clients: list[tuple[str, Any]] = []
        for provider in chain:
            client = self._create_client_for_role("cloud_frontier", provider)
            if client is not None:
                clients.append((provider, client))
        if not clients and self._anthropic_key:
            role = self.cfg.role_config("cloud_frontier")
            clients.append((
                "anthropic_legacy",
                LLM(
                    api_key=self._anthropic_key,
                    model=role.get("model", "claude-sonnet-4-6"),
                    max_tokens=int(role.get("max_tokens", 1024)),
                    temperature=float(role.get("temperature", 0)),
                ),
            ))
        if not clients:
            raise RuntimeError(
                "Cloud LLM not configured. Set a provider API key in .env "
                "(see configs/router.yaml and .env.example)."
            )
        result: Any = FailoverLLMClient(clients) if len(clients) > 1 else clients[0][1]
        self._clients[cache_key] = result
        if self._cloud is None:
            self._cloud = result
        return result

    def _get_or_create_local(self) -> LocalHTTPClient:
        if "local_fast" not in self._clients:
            role = self.cfg.role_config("local_fast")
            self._clients["local_fast"] = LocalHTTPClient(
                endpoint=str(role.get("endpoint", "http://localhost:11434/api/chat")),
                model=str(role.get("model", "llama3.2:3b")),
                max_tokens=int(role.get("max_tokens", 512)),
                temperature=float(role.get("temperature", 0)),
                timeout=float(role.get("timeout_seconds", 30)),
                native_tools=bool(role.get("native_tools", False)),
            )
        return self._clients["local_fast"]

    def _get_or_create_vision(self) -> VisionLLM:
        return self._get_or_create_vision_with_failover()

    def _get_or_create_vision_with_failover(self) -> VisionLLM:
        if "vision" in self._clients:
            return self._clients["vision"]
        role = self.cfg.role_config("vision")
        backend = str(role.get("backend", "anthropic")).lower()
        if backend == "ocr_only":
            cloud = self._get_cloud_with_failover()
        else:
            chain = self._provider_chain("vision")
            clients: list[tuple[str, Any]] = []
            for provider in chain:
                client = self._create_client_for_role("vision", provider)
                if client is not None:
                    clients.append((provider, client))
            if clients:
                cloud = FailoverLLMClient(clients) if len(clients) > 1 else clients[0][1]
            else:
                cloud = self._get_cloud_with_failover()
        vision = VisionLLM(
            cloud_llm=cloud,
            ocr_only=bool(role.get("ocr_only", False)),
            vlm_endpoint=role.get("vlm_endpoint"),
            vlm_model=role.get("vlm_model"),
        )
        self._clients["vision"] = vision
        return vision

    def invalidate_local_cache(self) -> None:
        """Re-check local model availability (e.g. after Ollama starts)."""
        self._local_available = None


# Back-compat alias
ModelRouter = Router
