"""The Phase 2 agent loop: dual-loop perceive→route→reason→gate→act→verify→observe.

Fast loop (local_fast): reflexive steps when AX is sufficient.
Slow loop (cloud_frontier): planning, recovery, novel goals.
Vision path: OCR/VLM when AX misses.

Both loops share the WorldModel blackboard.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from .config import Config, ROOT
from .llm import LLM
from .router import Router, RouteTier, RouterConfig
from .world_model import VerificationExpectation, WorldModel
from .policy import Policy, PolicyConfig, normalize_file_roots
from .planner import plan_goal, replan
from . import stop as stop_ctl
from .audit_log import AuditLog
from ..knowledge import loader as knowledge
from ..memory.store import MemoryStore
from ..memory.skills import SkillStore
from ..tools.registry import AgentContext, Registry, DEFAULT_REGISTRY
from ..tools.mcp_client import MCPClient
from ..voice.tts import TTS
from ..core.metrics import MetricsCollector

if TYPE_CHECKING:
    from ..hud.overlay import HUD

def record_usage_for_response(metrics, resp) -> None:  # noqa: ANN001
    """Record token usage for an LLMResponse if the backend reported any."""
    if resp is None:
        return
    in_tok = getattr(resp, "input_tokens", None)
    out_tok = getattr(resp, "output_tokens", None)
    if in_tok is None and out_tok is None:
        return
    try:
        metrics.record_llm_usage(getattr(resp, "backend", "unknown"), in_tok, out_tok)
    except Exception:  # noqa: BLE001 — metrics must never break the agent loop
        pass


BASE_SYSTEM_PROMPT = """You are Aether, an AI agent that operates a real macOS computer \
on the user's behalf using the provided tools.

Operating rules:
- Call `get_screen_context` BEFORE acting, and again after any action that changes \
the screen, so your targets are current.
- If AX tree is empty or element not found, use `analyze_screen` for OCR/vision fallback.
- Prefer clicking by `element_index` (from get_screen_context) over raw coordinates.
- For web tasks prefer `browser_*` tools; for Mail/Safari/Finder prefer tier-1 tools.
- For complex coding tasks use `delegate_to_coder` (Tier-0 CLI agents).
- Reuse learned skills from memory when they match the goal.
- Do ONE tool call at a time and observe the result before the next.
- If verification fails, try a different approach (vision, AppleScript, browser).
- Keep going until the task is done, then call `finish` with a short, friendly \
spoken summary.
- Be careful: think about whether an action is reversible.
You are concise."""


class Agent:
    def __init__(
        self,
        config: Config,
        registry: Registry | None = None,
        hud: "HUD | None" = None,
    ):
        self.cfg = config
        self.confirm_async: Callable[[str], Awaitable[bool]] | None = None
        self.llm: LLM | None = None
        if config.anthropic_api_key:
            self.llm = LLM(
                api_key=config.anthropic_api_key,
                model=config.model,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
            )
        router_path = config.get("router", "config_path")
        self.router = Router(
            router_cfg=RouterConfig.load(ROOT / router_path if router_path else None),
            cloud_llm=self.llm,
            anthropic_api_key=config.anthropic_api_key,
            api_keys=config.api_keys,
        )
        self.tts = TTS(
            engine=config.tts,
            voice=config.tts_voice,
            model=config.tts_model,
            groq_api_key=config.groq_api_key,
        )
        perf = config.get("performance") or {}
        self.world = WorldModel(
            ax_cache_ttl_ms=float(perf.get("ax_cache_ttl_ms", 200)),
        )
        self.registry = registry or DEFAULT_REGISTRY
        from ..plugins.loader import load_plugins

        load_plugins(self.registry, ROOT, config=config.raw)
        mem_cfg = config.get("memory") or {}
        embed_provider = str(mem_cfg.get("embedding_provider", "hash"))
        if config.memory_enabled:
            self.memory = MemoryStore(
                config.memory_db_path,
                embedding_provider=embed_provider,
                openai_api_key=config.openai_api_key,
                openai_model=str(mem_cfg.get("openai_model", "text-embedding-3-small")),
                local_model=str(mem_cfg.get("local_model", "all-MiniLM-L6-v2")),
            )
        else:
            self.memory = None
        skills_cfg = config.get("skills") or {}
        if skills_cfg.get("enabled", True):
            self.skills = SkillStore(
                skills_cfg.get("db_path"),
                embedding_provider=embed_provider,
                openai_api_key=config.openai_api_key,
            )
        else:
            self.skills = None
        self.metrics = MetricsCollector.get()
        caps = config.get("capabilities") or {}
        policy_raw = config.get("policy") or {}
        self.policy = Policy(PolicyConfig(
            careful=config.careful,
            capabilities=caps,
            approved_file_roots=normalize_file_roots(policy_raw.get("approved_file_roots")),
            network_allowlist=policy_raw.get("network_allowlist") or [],
            redact_secrets=bool(policy_raw.get("redact_secrets", True)),
            block_injection_goals=bool(policy_raw.get("block_injection_goals", True)),
            flag_injection_in_context=bool(policy_raw.get("flag_injection_in_context", True)),
            wrap_untrusted_context=bool(policy_raw.get("wrap_untrusted_context", True)),
        ))
        audit_raw = config.get("audit") or {}
        self.audit = AuditLog.configure(
            path=audit_raw.get("path"),
            enabled=bool(audit_raw.get("enabled", True)),
        )
        self.mcp = MCPClient({
            **(config.get("mcp") or {}),
            "careful": config.careful,
        })
        self.mcp.register_with_registry(self.registry.register_dynamic)
        self.hud = hud
        self.ctx = AgentContext(
            careful=config.careful,
            world=self.world,
            memory=self.memory,
            browser_headless=config.browser_headless,
        )

    def _system_prompt(self, goal: str) -> str:
        parts = [BASE_SYSTEM_PROMPT]
        if self.cfg.knowledge_enabled:
            pack = knowledge.prompt_slice(
                self.world.frontmost_app,
                goal,
                bundle_id=self.world.bundle_id,
            )
            if pack:
                parts.append(pack)
        if self.memory:
            mem = self.memory.prompt_slice(goal)
            if mem:
                parts.append(mem)
        if self.skills:
            sk = self.skills.prompt_slice(goal)
            if sk:
                parts.append(sk)
        ctx = self.world.context_block()
        if ctx:
            parts.append(self.policy.prepare_context_for_model(ctx))
        return "\n\n".join(parts)

    def _hud_update(self, **kwargs) -> None:
        if self.hud:
            self.hud.update(**kwargs)

    def say(self, text: str) -> None:
        print(f"\n🔊 Aether: {text}\n")
        self.tts.speak(text)

    async def say_async(self, text: str) -> None:
        await asyncio.to_thread(self.say, text)

    async def _maybe_vision_context(self, decision) -> str | None:
        """Inject vision/OCR context when router picks vision tier."""
        if decision.tier != RouteTier.VISION:
            return None
        path = self.world.capture_screenshot()
        if not path:
            return None
        vision_client = self.router.pick_client(decision)
        if hasattr(vision_client, "analyze_screenshot"):
            ctx = await asyncio.to_thread(vision_client.analyze_screenshot, path)
            self.world.screen_content_class = getattr(
                vision_client, "last_content_class", "unknown"
            )
            return ctx
        from ..perception import ocr
        formatted, regions, (w, h) = await asyncio.to_thread(ocr.recognize, path)
        routing = self.router.cfg.routing
        content = ocr.classify_screen_content(
            regions, w, h,
            min_conf=float(routing.get("min_region_confidence", 0.3)),
            char_threshold=int(routing.get("text_heavy_char_threshold", 200)),
            coverage_threshold=float(routing.get("text_coverage_threshold", 0.05)),
        )
        self.world.screen_content_class = content["label"]
        self.world.text_heavy_score = content["score"]
        return formatted

    async def _reason_step(
        self,
        goal: str,
        messages: list[dict],
        step: int,
        *,
        ax_miss: bool = False,
        correction: str | None = None,
    ):
        """Route and call the appropriate LLM backend (dual-loop entry)."""
        force_local = self.cfg.local_only and not self.cfg.has_cloud_llm()
        decision = self.router.route(
            self.world,
            careful=self.cfg.careful or force_local,
            ax_miss=ax_miss,
            force_tier=RouteTier.LOCAL_FAST if force_local else None,
            force_local=force_local,
        )
        loop_label = decision.tier.value
        print(f"⟳ router: {loop_label} ({decision.reason})")
        self._hud_update(step=f"Step {step}: {loop_label}…")

        system = self._system_prompt(goal)
        if correction:
            system += f"\n\nSELF-CORRECTION: {correction}"
        if self.world.needs_replan:
            system += (
                "\n\nThe last action did not produce the expected result. "
                "Re-plan and try a different approach."
            )

        vision_ctx = await self._maybe_vision_context(decision)
        if vision_ctx:
            system += f"\n\nVision/OCR context:\n{self.policy.redact_text(vision_ctx)}"

        client = self.router.pick_client(decision)
        abort = stop_ctl.abort_event()
        try:
            resp = await asyncio.to_thread(
                client.step,
                system,
                messages,
                self.registry.schemas(),
                abort_event=abort,
            )
        except stop_ctl.StopRequested:
            raise
        except RuntimeError as e:
            # Local unavailable → fallback to cloud
            if decision.tier == RouteTier.LOCAL_FAST:
                print(f"⚠️  {e} — falling back to cloud.")
                self.router.invalidate_local_cache()
                decision = self.router.route(
                    self.world, careful=True, force_tier=RouteTier.CLOUD_FRONTIER,
                )
                client = self.router.pick_client(decision)
                resp = await asyncio.to_thread(
                    client.step,
                    system,
                    messages,
                    self.registry.schemas(),
                    abort_event=abort,
                )
            else:
                raise
        record_usage_for_response(self.metrics, resp)
        return resp, decision.tier.value

    def _verification_for_tool(self, name: str, args: dict) -> VerificationExpectation | None:
        if name == "open_app":
            return VerificationExpectation(
                app_name=args.get("name"),
                frontmost_changed=True,
            )
        if name == "safari_open_url":
            return VerificationExpectation(contains_text=args.get("url", "")[:20])
        if name == "finder_go_to":
            return VerificationExpectation(contains_text=args.get("path", "")[-20:])
        return None

    def run(self, goal: str) -> str:
        """Sync entry point (backward compatible)."""
        return asyncio.run(self.run_async(goal))

    async def run_async(self, goal: str, *, run_id: str | None = None) -> str:
        stop_ctl.reset()
        self.world.set_goal(goal)
        self._hud_update(goal=goal, status="working", step="Starting…")

        rid = run_id or f"run-{int(time.time() * 1000)}"
        self.metrics.start_run(rid, goal)

        inj = self.policy.scan_injection(goal)
        if inj.flagged:
            self.audit.record(
                "injection_flag",
                run_id=rid,
                summary=goal[:200],
                injection_severity=inj.severity.value,
                extra={"matches": inj.matches},
            )
            self.metrics.inc("injection_flags")
        if self.policy.should_block_goal(goal):
            msg = "Blocked: prompt-injection pattern detected in your request."
            self.audit.record("run_blocked", run_id=rid, summary=msg)
            self.metrics.end_run("blocked")
            return msg

        self.audit.record("run_start", run_id=rid, summary=goal[:500])

        explicit_planner = bool(self.cfg.get("agent", "explicit_planner", default=False))
        planner_use_llm = bool(self.cfg.get("agent", "planner_use_llm", default=True))
        if explicit_planner:
            plan_llm = None
            if planner_use_llm and self.cfg.has_cloud_llm():
                decision = self.router.route(
                    self.world,
                    careful=True,
                    force_tier=None,
                )
                plan_llm = self.router.pick_client(decision)
            plan_result = await asyncio.to_thread(
                plan_goal,
                goal,
                self.world,
                llm=plan_llm,
                use_llm=planner_use_llm and plan_llm is not None,
            )
            if plan_result.steps:
                plan_preview = " → ".join(plan_result.steps[:4])
                print(f"📋 plan ({plan_result.source}): {plan_preview}")
                self._hud_update(step=f"Plan: {plan_preview}")

        messages: list[dict] = [{"role": "user", "content": goal}]
        final = ""
        task_success = False

        for step in range(1, self.cfg.max_steps + 1):
            step_start = time.time()
            if stop_ctl.is_set():
                final = "Stopped by user."
                self.world.mark_stopped()
                self._hud_update(status="stopped", step=final)
                self.say(final)
                self.metrics.end_run("stopped")
                break

            # Perceive
            percept_start = time.time()
            await asyncio.to_thread(self.world.refresh)
            percept_ms = (time.time() - percept_start) * 1000
            self.metrics.observe("percept_refresh_ms", percept_ms)
            self.metrics.warn_if_slow("percept_refresh_ms", percept_ms)
            ax_miss = self.world.ax_insufficient
            self.audit.record(
                "percept",
                run_id=rid,
                summary=(
                    f"app={self.world.frontmost_app} "
                    f"elements={self.world.element_count}"
                ),
            )

            try:
                resp, route_tier = await self._reason_step(
                    goal, messages, step, ax_miss=ax_miss,
                )
            except stop_ctl.StopRequested:
                final = "Stopped by user."
                self.world.mark_stopped()
                self._hud_update(status="stopped", step=final)
                self.say(final)
                self.metrics.end_run("stopped")
                break
            step_ms = (time.time() - step_start) * 1000
            self.metrics.record_step(route_tier, step_ms)
            self.metrics.warn_if_slow("step_latency_ms", step_ms)
            self.audit.record(
                "decision",
                run_id=rid,
                route_tier=route_tier,
                summary=(resp.text or "")[:300] if resp.text else f"tools={len(resp.tool_calls)}",
            )

            if resp.text:
                print(f"💭 {resp.text}")

            if not resp.tool_calls:
                final = resp.text or "Done."
                self.world.mark_idle()
                self._hud_update(status="idle", step="Done")
                await self.say_async(final)
                task_success = True
                break

            messages.append(LLM.assistant_turn(resp.raw_content))

            results = []
            done = False
            correction_note: str | None = None

            for call in resp.tool_calls:
                if stop_ctl.is_set():
                    final = "Stopped by user."
                    self.world.mark_stopped()
                    self._hud_update(status="stopped")
                    await self.say_async(final)
                    self.metrics.end_run("stopped")
                    return final

                name, args = call["name"], call["input"]
                self.world.record_tool_call(name, args)
                desc = self.registry.describe_call(name, args)
                print(f"→ step {step}: {desc}")
                self.world.record_action(desc)
                self._hud_update(step=desc, last_action=desc)

                if self.cfg.narrate and name not in (
                    "get_screen_context", "finish", "analyze_screen",
                ):
                    await self.say_async(desc)

                spec = self.registry.get(name)
                if spec and not self.policy.allows_tool(spec):
                    results.append({
                        "tool_use_id": call["id"],
                        "content": f"Permission denied for {name} ({spec.permission}).",
                    })
                    continue

                if spec and name == "run_shell":
                    if not self.policy.allows_shell_path(args.get("command", "")):
                        results.append({
                            "tool_use_id": call["id"],
                            "content": "Shell command blocked: path outside approved roots.",
                        })
                        continue

                if spec and self.policy.requires_confirm(spec, args):
                    if self.confirm_async is not None:
                        ok = await self.confirm_async(desc)
                    else:
                        ok = await asyncio.to_thread(self.policy.confirm, desc)
                    self.audit.record(
                        "confirmation",
                        run_id=rid,
                        tool=name,
                        confirmed=ok,
                        summary=desc[:200],
                    )
                    if not ok:
                        results.append({
                            "tool_use_id": call["id"],
                            "content": "User declined this action.",
                        })
                        continue

                if name == "finish":
                    final = args.get("message", "Done.")
                    results.append({"tool_use_id": call["id"], "content": final})
                    done = True
                    task_success = True
                    continue

                # Verify-after-act: snapshot before mutating tools
                if spec and spec.impact != "read":
                    exp = self._verification_for_tool(name, args)
                    self.world.begin_action_verification(exp)

                tool_start = time.time()
                try:
                    observation = await asyncio.to_thread(
                        self.registry.dispatch, name, args, self.ctx
                    )
                    tool_err = observation.startswith("ERROR")
                except stop_ctl.StopRequested:
                    final = "Stopped by user."
                    self.world.mark_stopped()
                    self._hud_update(status="stopped")
                    await self.say_async(final)
                    self.metrics.end_run("stopped")
                    return final

                self.metrics.record_tool(
                    name, (time.time() - tool_start) * 1000, error=tool_err
                )
                first_line = observation.splitlines()[0][:120] if observation else ""
                self.audit.record(
                    "action",
                    run_id=rid,
                    tool=name,
                    tool_args=args,
                    summary=first_line if not tool_err else f"ERROR: {first_line}",
                )
                print(f"   ↳ {first_line}")
                self.world.record_observation(observation)

                verified = self.world.verify(None, observation)
                if not verified:
                    fail_msg = (
                        f"VERIFY FAILED after {name}: screen state did not change "
                        f"as expected (failures={self.world.step_failure_count}). "
                        "Try analyze_screen, browser tools, or AppleScript."
                    )
                    print(f"   ⚠️ {fail_msg}")
                    observation = observation + "\n\n" + fail_msg
                    correction_note = fail_msg

                results.append({"tool_use_id": call["id"], "content": observation})

                # AX miss detection for click
                if name == "click" and "not found" in observation.lower():
                    correction_note = observation

            messages.append(LLM.tool_results_turn(results))

            if correction_note and self.world.needs_replan:
                if explicit_planner:
                    plan_llm = None
                    if planner_use_llm and self.cfg.has_cloud_llm():
                        decision = self.router.route(
                            self.world, careful=True, ax_miss=True,
                        )
                        plan_llm = self.router.pick_client(decision)
                    replan_result = await asyncio.to_thread(
                        replan,
                        goal,
                        self.world,
                        llm=plan_llm,
                        use_llm=planner_use_llm and plan_llm is not None,
                        failure_context=correction_note,
                    )
                    if replan_result.steps:
                        print(f"📋 replan: {' → '.join(replan_result.steps[:4])}")
                # Self-correction: extra reasoning turn with failure context
                fix_resp, _ = await self._reason_step(
                    goal, messages, step,
                    ax_miss=True,
                    correction=correction_note,
                )
                if fix_resp.tool_calls:
                    messages.append(LLM.assistant_turn(fix_resp.raw_content))
                elif fix_resp.text:
                    print(f"💭 correction: {fix_resp.text}")

            if done:
                self.world.mark_idle()
                self._hud_update(status="idle", step="Done")
                await self.say_async(final)
                break
        else:
            final = "Reached the step limit before finishing. Stopping for safety."
            self.world.mark_idle()
            self._hud_update(status="idle", step=final)
            await self.say_async(final)

        # Long-term memory: store successful traces
        if self.memory and task_success and self.world.task_trace():
            self.memory.store_task_trace(goal, self.world.task_trace(), success=True)
        if self.skills and task_success:
            trace = self.world.tool_trace()
            if trace:
                skill_id = self.skills.distill_from_trace(goal, trace)
                if skill_id:
                    print(f"📚 Learned skill id={skill_id}")

        self.metrics.end_run("idle" if task_success else "incomplete")
        self.audit.record(
            "run_end",
            run_id=rid,
            summary=final[:300],
            extra={"success": task_success},
        )

        from ..effectors import browser as browser_fx
        browser_fx.close_session()

        return final
