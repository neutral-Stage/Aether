"""Unit tests for provider failover chain (Phase 10, NFR-3)."""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from aether.core.llm import LLMResponse
from aether.core.llm_errors import FailoverLLMClient, is_transient_llm_error
from aether.core.router import RouteTier, Router, RouterConfig


@dataclass
class _FakeClient:
    name: str
    fail_with: str | None = None
    backend: str = "fake"

    def step(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        if self.fail_with == "transient":
            raise RuntimeError("HTTP 503 Service Unavailable")
        if self.fail_with == "fatal":
            raise ValueError("invalid request")
        return LLMResponse(
            text=f"ok from {self.name}",
            tool_calls=[],
            raw_content=[],
            stop_reason="end_turn",
            backend=self.name,
        )


@pytest.mark.unit
class TestTransientErrors:
    def test_detects_503_message(self) -> None:
        assert is_transient_llm_error(RuntimeError("HTTP 503 Service Unavailable"))

    def test_detects_rate_limit(self) -> None:
        assert is_transient_llm_error(RuntimeError("rate limit exceeded"))

    def test_non_transient(self) -> None:
        assert not is_transient_llm_error(ValueError("bad api key"))


@pytest.mark.unit
class TestFailoverLLMClient:
    def test_falls_through_on_transient(self) -> None:
        clients = [
            ("primary", _FakeClient("primary", fail_with="transient")),
            ("backup", _FakeClient("backup")),
        ]
        wrapper = FailoverLLMClient(clients)
        resp = wrapper.step("sys", [], [])
        assert resp.text == "ok from backup"

    def test_raises_on_fatal(self) -> None:
        clients = [
            ("primary", _FakeClient("primary", fail_with="fatal")),
            ("backup", _FakeClient("backup")),
        ]
        wrapper = FailoverLLMClient(clients)
        with pytest.raises(ValueError, match="invalid request"):
            wrapper.step("sys", [], [])


@pytest.mark.unit
class TestRouterFailover:
    def test_provider_chain_from_role(self) -> None:
        raw = {
            "providers": {
                "zai": {"backend": "openai_compatible", "api_key_env": "ZAI_API_KEY"},
                "openrouter": {"backend": "openai_compatible", "api_key_env": "OPENROUTER_API_KEY"},
            },
            "roles": {
                "cloud_frontier": {
                    "provider": "zai",
                    "failover_providers": ["openrouter"],
                },
            },
        }
        router = Router(router_cfg=RouterConfig(raw=raw), api_keys={})
        chain = router._provider_chain("cloud_frontier")  # noqa: SLF001
        assert chain == ["zai", "openrouter"]

    @patch("aether.core.router.create_client")
    def test_cloud_failover_wrapper(self, mock_create: MagicMock) -> None:
        primary = MagicMock()
        backup = MagicMock()
        mock_create.side_effect = [primary, backup]
        raw = {
            "providers": {
                "zai": {"backend": "openai_compatible"},
                "openrouter": {"backend": "openai_compatible"},
            },
            "roles": {
                "cloud_frontier": {
                    "provider": "zai",
                    "failover_providers": ["openrouter"],
                },
            },
        }
        router = Router(router_cfg=RouterConfig(raw=raw), api_keys={"ZAI_API_KEY": "x"})
        router._clients.clear()  # noqa: SLF001
        client = router.pick_client_with_failover(RouteTier.CLOUD_FRONTIER)
        assert isinstance(client, FailoverLLMClient)

    @patch("aether.core.router.create_client")
    def test_single_provider_no_wrapper(self, mock_create: MagicMock) -> None:
        fake = _FakeClient("only")
        mock_create.return_value = fake
        raw = {
            "providers": {"zai": {"backend": "openai_compatible"}},
            "roles": {"cloud_frontier": {"provider": "zai"}},
        }
        router = Router(router_cfg=RouterConfig(raw=raw), api_keys={"ZAI_API_KEY": "x"})
        router._clients.clear()  # noqa: SLF001
        client = router.pick_client_with_failover(RouteTier.CLOUD_FRONTIER)
        assert client is fake
