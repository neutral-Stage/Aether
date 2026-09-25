"""The Phase 2 agent loop: dual-loop perceive→route→reason→gate→act→verify→observe.

Fast loop (local_fast): reflexive steps when AX is sufficient.
Slow loop (cloud_frontier): planning, recovery, novel goals.
Vision path: OCR/VLM when AX misses.

Both loops share the WorldModel blackboard.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .config import Config, ROOT
from .llm import (INVALID_ARGS_KEY, LLM, TokenGate, collapse_tool_results, prune_images,
                  step_with_tokens)
from .router import Router, RouteTier, RouterConfig
from .world_model import VerificationExpectation, WorldModel
from .focus import FocusTracker
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

log = logging.getLogger(__name__)

def record_usage_for_response(metrics, resp) -> None:  # noqa: ANN001
    """Record token usage for an LLMResponse if the backend reported any."""
    if resp is None:
        return
    in_tok = getattr(resp, "input_tokens", None)
    out_tok = getattr(resp, "output_tokens", None)
    if in_tok is None and out_tok is None:
        return
    try:
        metrics.record_llm_usage(getattr(resp, "backend", "unknown"), in_tok, out_tok,
                                 model=getattr(resp, "model", None))
    except Exception:  # noqa: BLE001 — metrics must never break the agent loop
        pass


_FALLBACK_SYSTEM_PROMPT = (
    "You are Aether, an AI agent that operates a real macOS computer on the user's "
    "behalf using the provided tools. Call get_screen_context before acting, make one "
    "tool call at a time, and call finish with a short summary when the task is done."
)


# "Said it but didn't do it": a first-person promise of an action, in a reply
# that made no tool call (Samuel's guard). "Let me know …" is not a promise.
_PROMISE_RE = re.compile(
    r"\b(?:I'll|I will|I'm going to|I am going to|let me(?! know)|"
    r"(?:next|now),? I(?:'ll| will))\s+(?:\w+\s+){0,3}?"
    r"(?:open|click|type|press|run|search|navigate|go|select|create|send|move|check|look|"
    r"find|scroll|launch|write|save|close|start|try|use|drag|copy|paste|enter|fill|delete|"
    r"switch|read|add|set|change|install|download|upload)\b",
    re.IGNORECASE)
SAY_DO_NUDGE = ("You described an action but made no tool call, so nothing happened. "
                "Make the tool call now, or call finish if the task is already done.")


@dataclass
class CallOutcome:
    """What one tool call produced, for the tool_result the model sees."""

    content: str
    images: list[str] = field(default_factory=list)
    finished: bool = False       # the finish tool
    correction: str | None = None
    gui_changed: bool = False
    error: bool = False


# UI tools whose effect is not visible in the accessibility tree.
_NO_SCREEN_CHECK = frozenset({"clipboard_set", "set_volume", "hover"})
MAX_BATCH = 5
# Reversible UI actions that may be chained without a model turn in between.
BATCHABLE_TOOLS = frozenset({
    "click", "click_element", "click_text", "click_mark", "type_text", "press_key",
    "scroll", "hover", "drag", "wait", "menu_item", "focus_window",
})
_SECRET_ASK_RE = re.compile(
    r"\b(?:password|passcode|passphrase|pin(?:\s+code)?|one[- ]time\s+code|2fa|mfa|"
    r"verification\s+code|security\s+code|cvv|cvc|card\s+number|social\s+security|"
    r"seed\s+phrase|recovery\s+(?:phrase|key)|private\s+key|api\s+key)\b", re.I)


def clean_history(history: list[dict] | None, max_turns: int = 20) -> list[dict]:
    """Earlier conversation turns as strictly alternating user/assistant text
    ending with an assistant turn (so the new goal follows as a user turn)."""
    out: list[dict] = []
    for turn in (history or [])[-max_turns:]:
        role = turn.get("role") if isinstance(turn, dict) else None
        text = str(turn.get("content") or "").strip() if isinstance(turn, dict) else ""
        if role not in ("user", "assistant") or not text:
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n" + text[:4000]
        else:
            out.append({"role": role, "content": text[:4000]})
    while out and out[0]["role"] != "user":
        out.pop(0)
    if out and out[-1]["role"] != "assistant":
        out.pop()
    return out


def _stdin_answer(question: str, options: list[str]) -> str | None:
    """CLI fallback for ask_user."""
    import sys

    if not sys.stdin or not sys.stdin.isatty():
        return None
    hint = f" [{' / '.join(options)}]" if options else ""
    try:
        return input(f"\n❓ {question}{hint}\n> ")
    except (EOFError, OSError):
        return None


def promises_action(text: str | None) -> bool:
    return bool(text and _PROMISE_RE.search(text))


def load_system_prompt() -> str:
    """The operating rules, from the single source shared/prompts/system.txt."""
    try:
        text = (ROOT / "shared" / "prompts" / "system.txt").read_text(encoding="utf-8").strip()
    except OSError:
        log.warning("shared/prompts/system.txt missing; using the minimal prompt")
        return _FALLBACK_SYSTEM_PROMPT
    return text or _FALLBACK_SYSTEM_PROMPT


BASE_SYSTEM_PROMPT = load_system_prompt()


class Agent:
    def __init__(
        self,
        config: Config,
        registry: Registry | None = None,
        hud: "HUD | None" = None,
    ):
        self.cfg = config
        self.confirm_async: Callable[[str], Awaitable[bool]] | None = None
        # ask_user hook: (question, options) -> answer, or None on timeout/skip.
        # The sidecar sets it (question event + POST /answer); CLI uses stdin.
        self.ask_async: Callable[[str, list[str]], Awaitable[str | None]] | None = None
        # Structured run events (tool_call, tool_result, screenshot, text, plan,
        # question) for the app's step log; no-op unless the sidecar sets it.
        self.emit: Callable[[dict], None] | None = None
        # All model clients come from the router (configs/router.yaml). The old
        # eager Anthropic client here was never used and was built with the
        # supervisor's model id (a non-Anthropic model).
        router_path = config.get("router", "config_path")
        self.router = Router(
            router_cfg=RouterConfig.load(ROOT / router_path if router_path else None),
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
        # Self-written tools (aether/toolsmith): register the installed my_* tools.
        from ..toolsmith.settings import Settings as ToolsmithSettings
        from ..tools import toolsmith_tools

        self.toolsmith = ToolsmithSettings.from_raw(config.raw)
        toolsmith_tools.sync_registry(self.registry, self.toolsmith)
        self._tool_repairs: dict[str, int] = {}
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
            trusted_shortcuts=[str(n) for n in policy_raw.get("trusted_shortcuts") or []],
            redact_secrets=bool(policy_raw.get("redact_secrets", True)),
            block_injection_goals=bool(policy_raw.get("block_injection_goals", True)),
            flag_injection_in_context=bool(policy_raw.get("flag_injection_in_context", True)),
            wrap_untrusted_context=bool(policy_raw.get("wrap_untrusted_context", True)),
        ))
        # Tracks where the next synthetic keystroke/click lands, so the policy
        # can tell `type_text` into a shell from `type_text` into a text field.
        self.focus = FocusTracker()
        # Consent ledger: one Rule-of-Two confirmation per identical payload per
        # run. Cross-call state, so it cannot live in Policy as a pure function.
        self._ro2_grants: set[tuple[str, str]] = set()
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
            browser_attach_mode=config.browser_attach_mode,
            browser_cdp_url=config.browser_cdp_url,
        )
        self.ctx.locate = self._locate

    def _record_pack_learning(self, goal: str) -> None:
        """Distill a successful run into an app-specific learned recipe (Phase 10)."""
        try:
            from ..knowledge import learned
            from ..knowledge import loader as kloader
            key = kloader.resolve_pack_key(self.world.frontmost_app, self.world.bundle_id)
            if not key:
                return
            name = learned.record_success(key, goal, self.world.task_trace(),
                                          tainted=bool(self.world.untrusted_seen))
            if name:
                print(f"📖 Learned recipe '{name}' for {self.world.frontmost_app}")
        except Exception:  # noqa: BLE001 — learning must never break a run
            pass

    _NEVER_GRANT = frozenset({"remember_fact", "watch_app", "spawn_agent",
                              "spawn_graph", "send_to_agent", "delegate_to_coder"})

    def _grant_key(self, name: str, args: dict, focus) -> tuple[str, str] | None:
        """Key on the EXACT whitespace-normalized literal payload. Keying on the
        head binary would let an approved `git diff` grant
        `git config --global alias.x '!sh'`; keying on host would let an
        approved https://ok.com/page grant https://ok.com/?d=SECRET."""
        if name in self._NEVER_GRANT:
            return None
        if name.startswith("my_"):
            # self-written tools take arbitrary arguments: grant only the exact call
            blob = json.dumps(args, sort_keys=True, default=str)
            return (name, hashlib.sha1(blob.encode()).hexdigest()[:12])
        blob = "|".join(
            f"{k}={' '.join(str(args[k]).split())}"
            for k in ("command", "source", "text", "url", "to", "subject",
                      "body", "key", "prompt")
            if k in args)
        if name == "click":
            blob += "|label=" + (getattr(focus, "label", "") or "")
        return (name, hashlib.sha1(blob.encode()).hexdigest()[:12])

    async def _confirm(self, text: str) -> bool:
        if self.confirm_async is not None:
            return bool(await self.confirm_async(text))
        return bool(await asyncio.to_thread(self.policy.confirm, text))

    def _token_gate(self, step: int) -> TokenGate | None:
        """Stream the model's text to the app as `token` events (only when someone
        listens; JSON-looking turns are held back, see llm.TokenGate)."""
        if self.emit is None:
            return None
        return TokenGate(lambda text: self._emit({"type": "token", "step": step,
                                                  "text": text}))

    def _schemas(self) -> list[dict]:
        """Tool schemas offered to the model (make_tool only when the toolsmith is on)."""
        schemas = self.registry.schemas()
        if not self.toolsmith.enabled:
            schemas = [s for s in schemas if s.get("name") != "make_tool"]
        return schemas

    def _emit(self, event: dict) -> None:
        if self.emit is None:
            return
        try:
            self.emit(event)
        except Exception:  # noqa: BLE001 — the step log must never break a run
            log.debug("emit failed", exc_info=True)

    async def _execute_call(self, name: str, args: dict, *, step: int, rid: str,
                            in_batch: bool = False) -> CallOutcome:
        """Validate, gate, dispatch and verify ONE tool call.

        Every path to a tool goes through here: top-level calls, each action
        of batch_actions, and nothing else, so the policy gate cannot be
        skipped. Raises stop_ctl.StopRequested when STOP is pressed.
        """
        if stop_ctl.is_set():
            raise stop_ctl.StopRequested()
        args = args if isinstance(args, dict) else {}
        self.world.record_tool_call(name, args)
        desc = self.registry.describe_call(name, args)
        print(f"→ step {step}: {desc}")
        self.world.record_action(desc)
        self._hud_update(step=desc, last_action=desc)

        if self.cfg.narrate and name not in (
            "get_screen_context", "finish", "analyze_screen", "batch_actions",
        ):
            await self.say_async(desc)

        spec = self.registry.get(name)
        if INVALID_ARGS_KEY in args:
            return CallOutcome(
                f"ERROR: the arguments for {name} were not valid JSON "
                f"({str(args[INVALID_ARGS_KEY])[:120]!r}). Call {name} again with a "
                "JSON object that matches its schema.", error=True)
        missing = [k for k in ((spec.json_schema.get("required") or []) if spec else [])
                   if k not in args]
        if missing:
            return CallOutcome(f"ERROR: {name} needs {', '.join(missing)}. Call it again "
                               "with every required argument.", error=True)
        if spec and not self.policy.allows_tool(spec):
            return CallOutcome(f"Permission denied for {name} ({spec.permission}).", error=True)

        if name == "batch_actions":
            if in_batch:
                return CallOutcome("ERROR: batch_actions cannot be nested.", error=True)
            return await self._execute_batch(args, step=step, rid=rid)
        if name == "ask_user":
            return await self._ask_user(args, rid=rid)
        if name == "point_at":
            return await self._point_at(args, step=step)
        if name == "make_tool":
            return await self._make_tool(args, step=step, rid=rid)

        shell_text = self.policy.shell_payload(name, args) if spec else None
        if shell_text is not None and not self.policy.allows_shell_path(shell_text):
            return CallOutcome("Shell command blocked: path outside approved roots.", error=True)
        blocked_path = next((fp for fp in self.policy.file_paths(name, args)
                             if not self.policy.allows_file_path(fp)), None)
        if blocked_path is not None:
            return CallOutcome(f"Blocked: {blocked_path} is outside the approved folders "
                               "(policy.approved_file_roots).", error=True)

        untrusted = self._context_is_untrusted()
        focus = self.focus.state()
        if spec and name in ("click", "click_mark"):
            focus = focus.with_label(self._click_label(args, name))
        ro2 = bool(spec and self.policy.is_rule_of_two_risk(spec, args, untrusted, focus))
        # Ask once per identical payload per run. Applies ONLY to rule-of-two
        # confirmations — never to destructive or careful mode, and never to
        # the _NEVER_GRANT tools.
        if ro2 and spec and not self.policy.requires_confirm(spec, args, focus):
            key = self._grant_key(name, args, focus)
            if key is not None:
                if key in self._ro2_grants:
                    ro2 = False
                else:
                    self._ro2_grants.add(key)
        if spec and (self.policy.requires_confirm(spec, args, focus) or ro2):
            # Surface the EXACT operation for destructive / rule-of-two actions
            # so injected screen text can't disguise the ask.
            if ro2 or self.policy.impact_of(spec, args, focus) == "destructive":
                confirm_text = self.policy.describe_operation(spec, args, focus)
                if ro2:
                    # Name the source: taint is sticky, so a confirm can land
                    # several steps after the read that caused it.
                    via = getattr(self.world, "untrusted_source", "") or "context"
                    confirm_text = (f"⚠️ This run read untrusted content (via {via}). "
                                    "Approve this EXACT action?\n" + confirm_text)
            else:
                confirm_text = desc
            ok = await self._confirm(confirm_text)
            self.audit.record("confirmation", run_id=rid, tool=name, confirmed=ok,
                              summary=confirm_text[:200], extra={"rule_of_two": ro2})
            if not ok:
                return CallOutcome("User declined this action.", error=True)

        # Update focus AFTER the gate (this call was judged against the PREVIOUS
        # state) and BEFORE dispatch, so the next call is gated against where
        # this one leaves the input target.
        self.focus.observe(name, args, self.world)

        if name == "finish":
            return CallOutcome(str(args.get("message", "Done.")), finished=True)

        # Verify-after-act: snapshot before actions that should change the screen.
        # Shell, file, network, agent and self-written tools leave the UI alone, so
        # an unchanged screen after them is not a failure.
        expectation = self._verification_for_tool(name, args)
        if spec and spec.impact != "read" and (
                expectation is not None
                or (spec.permission == "input" and name not in _NO_SCREEN_CHECK)):
            self.world.begin_action_verification(expectation)

        self._emit({"type": "tool_call", "step": step, "tool": name, "description": desc})
        tool_start = time.time()
        self.ctx.pending_images = []
        if name.startswith("my_") and self.toolsmith.enabled:
            observation = await self._run_my_tool(name, args, step=step, rid=rid)
        else:
            observation = await asyncio.to_thread(self.registry.dispatch, name, args, self.ctx)
        tool_err = observation.startswith("ERROR")
        self.metrics.record_tool(name, (time.time() - tool_start) * 1000, error=tool_err)
        first_line = observation.splitlines()[0][:120] if observation else ""
        self.audit.record("action", run_id=rid, tool=name, tool_args=args,
                          summary=first_line if not tool_err else f"ERROR: {first_line}")
        print(f"   ↳ {first_line}")
        self.world.record_observation(observation, source=name)

        correction = None
        if not self.world.verify(None, observation):
            fail_msg = (f"VERIFY FAILED after {name}: screen state did not change as "
                        f"expected (failures={self.world.step_failure_count}). "
                        "Try analyze_screen, browser tools, or AppleScript.")
            print(f"   ⚠️ {fail_msg}")
            observation = observation + "\n\n" + fail_msg
            correction = fail_msg
        # AX miss detection for click
        if name == "click" and "not found" in observation.lower():
            correction = observation

        images, self.ctx.pending_images = list(self.ctx.pending_images), []
        self._emit({"type": "tool_result", "step": step, "tool": name, "ok": not tool_err,
                    "summary": first_line})
        for img in images:
            self._emit({"type": "screenshot", "step": step, "tool": name, "path": img})
        gui = bool(spec and spec.permission == "input" and spec.impact != "read" and not tool_err)
        return CallOutcome(observation, images=images, correction=correction,
                           gui_changed=gui, error=tool_err)

    async def _execute_batch(self, args: dict, *, step: int, rid: str) -> CallOutcome:
        """Run up to 5 reversible UI actions in a row, each through the full gate;
        stop at the first one that fails, is blocked or is declined."""
        actions = args.get("actions")
        if not isinstance(actions, list) or not actions:
            return CallOutcome("ERROR: actions must be a non-empty list of "
                               "{tool, args} objects.", error=True)
        if len(actions) > MAX_BATCH:
            return CallOutcome(f"ERROR: at most {MAX_BATCH} actions per batch.", error=True)
        lines: list[str] = []
        images: list[str] = []
        gui = False
        correction = None
        for n, action in enumerate(actions, 1):
            tool = str(action.get("tool") or "") if isinstance(action, dict) else ""
            sub_args = action.get("args") if isinstance(action, dict) else None
            sub_args = sub_args if isinstance(sub_args, dict) else {}
            if tool not in BATCHABLE_TOOLS:
                lines.append(f"{n}. {tool or '?'}: ERROR not allowed in a batch "
                             f"(allowed: {', '.join(sorted(BATCHABLE_TOOLS))}). Stopped.")
                break
            out = await self._execute_call(tool, sub_args, step=step, rid=rid, in_batch=True)
            first = out.content.splitlines()[0][:160] if out.content else ""
            lines.append(f"{n}. {self.registry.describe_call(tool, sub_args)}: {first}")
            images = out.images or images
            gui = gui or out.gui_changed
            correction = out.correction or correction
            if out.error or out.content.startswith(("ERROR", "User declined", "Blocked")):
                lines.append(f"Stopped after action {n}; the rest did not run.")
                return CallOutcome("\n".join(lines), images=images, correction=correction,
                                   gui_changed=gui, error=True)
        return CallOutcome("\n".join(lines), images=images, correction=correction,
                           gui_changed=gui)

    async def _point_at(self, args: dict, *, step: int) -> CallOutcome:
        """Resolve a target and send it to the app's overlay (a pointer event)."""
        from ..perception.pointing import Target
        from ..tools import targeting_tools

        try:
            x, y, w, h, label = await asyncio.to_thread(
                targeting_tools.resolve_point_target, args, self.ctx)
        except (ValueError, RuntimeError) as e:
            return CallOutcome(f"ERROR: {e}", error=True)
        target = Target("point", x, y, label, w, h, source="ax" if w else "screen")
        self._emit({"type": "pointer", "step": step, "source": "agent",
                    "targets": [target.as_dict()]})
        return CallOutcome(f"Pointing at '{label or 'that spot'}' for the user at "
                           f"({int(x)}, {int(y)}).")

    async def _ask_user(self, args: dict, *, rid: str) -> CallOutcome:
        """Ask the user a question and wait (STOP-aware) for the answer."""
        question = " ".join(str(args.get("question") or "").split())[:500]
        options = [str(o)[:80] for o in (args.get("options") or []) if str(o).strip()][:6]
        if not question:
            return CallOutcome("ERROR: question is required.", error=True)
        if _SECRET_ASK_RE.search(question):
            return CallOutcome(
                "ERROR: Aether never asks for passwords, passcodes, verification codes or "
                "card numbers. Tell the user to enter it themselves, then continue.",
                error=True)
        self.audit.record("question", run_id=rid, summary=question[:200])
        self._emit({"type": "question", "question": question, "options": options})
        self._hud_update(step=f"Question: {question[:80]}")
        await self.say_async(question)
        if self.ask_async is not None:
            task = asyncio.ensure_future(self.ask_async(question, options))
        else:
            task = asyncio.ensure_future(asyncio.to_thread(_stdin_answer, question, options))
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=0.5)
                if done:
                    answer = task.result()
                    break
                if stop_ctl.is_set():
                    task.cancel()
                    raise stop_ctl.StopRequested()
        except asyncio.CancelledError:
            answer = None
        if not answer or not str(answer).strip():
            return CallOutcome("The user did not answer. Continue with your best judgment "
                               "if it is safe, or finish and say what you need.")
        answer = str(answer).strip()[:2000]
        # The answer is the user's own words: trusted, but still redacted like
        # everything else that reaches the model.
        return CallOutcome(f"The user answered: {self.policy.redact_text(answer)}")

    # ---- self-written tools (aether/toolsmith) ------------------------------------------

    def _toolsmith_client(self):  # noqa: ANN202
        return self.router.pick_client_with_failover(RouteTier.CLOUD_FRONTIER)

    def _record_usage(self, resp) -> None:  # noqa: ANN001
        record_usage_for_response(self.metrics, resp)

    async def _make_tool(self, args: dict, *, step: int, rid: str) -> CallOutcome:
        """Propose → the user approves the exact capabilities → write → check →
        independent review → install → register. Nothing runs before approval."""
        from ..toolsmith import executor, generate, store
        from ..tools import toolsmith_tools

        ts = self.toolsmith
        if not ts.enabled:
            return CallOutcome("ERROR: self-written tools are turned off "
                               "(toolsmith.enabled in config.yaml).", error=True)
        runnable, why = executor.can_run(ts)
        if not runnable:
            return CallOutcome(f"ERROR: tools cannot be made here: {why}. Do the task "
                               "another way.", error=True)
        manifest = toolsmith_tools.manifest_from_args(args)
        if not manifest.network:
            manifest.read_dirs = []        # only enforced for tools with internet access
        errors = manifest.validate(ts.approved_roots)
        if not manifest.how:
            errors.append("'how' is required: say how the tool should work")
        if manifest.network and not ts.network_cap:
            errors.append("internet access is off for Aether (capabilities.network)")
        if errors:
            return CallOutcome("ERROR: the tool proposal needs changes:\n- "
                               + "\n- ".join(errors), error=True)
        existing = store.load(manifest.name)
        confirm_text = toolsmith_tools.approval_text(manifest, replacing=existing is not None)
        if self._context_is_untrusted():
            via = getattr(self.world, "untrusted_source", "") or "context"
            confirm_text = (f"⚠️ This run read untrusted content (via {via}). Only approve "
                            "a tool you asked for.\n" + confirm_text)
        self._hud_update(step=f"Asking to create {manifest.name}")
        ok = await self._confirm(confirm_text)
        self.audit.record("confirmation", run_id=rid, tool="make_tool", confirmed=ok,
                          summary=confirm_text[:200], extra={"tool_name": manifest.name})
        if not ok:
            return CallOutcome("The user declined this tool. Do the task another way.",
                               error=True)
        self._hud_update(step=f"Writing {manifest.name}…")
        self._emit({"type": "tool_call", "step": step, "tool": "make_tool",
                    "description": f"write {manifest.name}"})
        built = await asyncio.to_thread(
            generate.build, self._toolsmith_client(), manifest, manifest.how,
            abort_event=stop_ctl.abort_event(), on_response=self._record_usage)
        if stop_ctl.is_set():
            raise stop_ctl.StopRequested()
        if not built.ok:
            why_not = "; ".join(built.problems)[:600] or "no usable code"
            self.audit.record("tool_rejected", run_id=rid, tool=manifest.name,
                              summary=why_not[:300])
            self._emit({"type": "tool_result", "step": step, "tool": "make_tool", "ok": False,
                        "summary": f"{manifest.name} not installed"})
            return CallOutcome(f"ERROR: {manifest.name} was not installed: {why_not}. Do the "
                               "task another way.", error=True)
        tool = await asyncio.to_thread(store.install, manifest, built.code)
        m = tool.manifest
        self.audit.record("tool_installed", run_id=rid, tool=m.name,
                          summary=f"v{m.version}: {m.capability_text()}"[:300],
                          extra={"version": m.version, "sha256": m.code_sha256,
                                 "network": m.network, "write_dirs": m.write_dirs,
                                 "read_dirs": m.read_dirs})
        self.registry.register(toolsmith_tools.spec_for(tool, ts))
        self._emit({"type": "tool_result", "step": step, "tool": "make_tool", "ok": True,
                    "summary": f"installed {m.name} v{m.version}"})
        return CallOutcome(f"Installed {m.signature()} (version {m.version}). It is one of "
                           "your tools now: call it to do the task.")

    async def _run_my_tool(self, name: str, args: dict, *, step: int, rid: str) -> str:
        """Run a self-written tool; on a bug, repair it (same manifest) and retry,
        at most toolsmith.max_repairs times per tool per run."""
        from ..toolsmith import executor, generate, store
        from ..toolsmith.manifest import ToolManifest

        ts = self.toolsmith
        tool = store.load(name)
        if tool is None:
            return f"ERROR: {name} is not installed any more."
        while True:
            result = await asyncio.to_thread(executor.run_tool, tool.manifest, tool.code(),
                                             args, ts, should_stop=stop_ctl.is_set)
            self.audit.record("tool_run", run_id=rid, tool=name,
                              summary=f"v{tool.manifest.version} {result.kind} "
                                      f"{result.duration_ms} ms",
                              extra={"version": tool.manifest.version, "kind": result.kind,
                                     "sandboxed": result.sandboxed})
            if result.kind == "stopped":
                raise stop_ctl.StopRequested()
            action = generate.diagnose(result)
            if action == "ok":
                return result.text(name)
            used = self._tool_repairs.get(name, 0)
            if action != "repair":
                return self._tool_failure_text(name, result, action, used)
            if used >= ts.max_repairs:
                return self._tool_failure_text(name, result, "give_up", used)
            if self._context_is_untrusted():
                return self._tool_failure_text(name, result, "tainted", used)
            self._tool_repairs[name] = used + 1
            self._hud_update(step=f"Repairing {name}…")
            self._emit({"type": "tool_call", "step": step, "tool": name,
                        "description": f"repair {name} ({result.kind})"})
            built = await asyncio.to_thread(
                generate.build, self._toolsmith_client(), tool.manifest, tool.manifest.how,
                previous_code=tool.code(), failure=generate.failure_report(result, args),
                abort_event=stop_ctl.abort_event(), on_response=self._record_usage)
            if stop_ctl.is_set():
                raise stop_ctl.StopRequested()
            if not built.ok:
                self.audit.record("tool_repair_rejected", run_id=rid, tool=name,
                                  summary="; ".join(built.problems)[:300])
                return self._tool_failure_text(name, result, "give_up", used + 1)
            # A repair changes the code, never what the tool may do.
            same = ToolManifest.from_dict(tool.manifest.to_dict())
            tool = await asyncio.to_thread(store.install, same, built.code)
            self.audit.record("tool_repaired", run_id=rid, tool=name,
                              summary=f"v{tool.manifest.version} after {result.kind}",
                              extra={"version": tool.manifest.version,
                                     "sha256": tool.manifest.code_sha256})

    @staticmethod
    def _tool_failure_text(name: str, result, action: str, repairs: int) -> str:  # noqa: ANN001
        base = f"ERROR: {name} failed ({result.kind}): {result.output[:600]}"
        if action == "args":
            return base + "\nCheck the arguments and call it again."
        if action == "ask":
            return (base + "\nIts sandbox blocked something it tried to do. If the task "
                    "really needs that access, propose a new version with make_tool that "
                    "declares it (the user will be asked); otherwise do the task another way.")
        if action == "tainted":
            return (base + "\nIt was not repaired automatically because this run read "
                    "untrusted content. Do the task another way.")
        if action == "give_up":
            return (base + f"\nIt was repaired {repairs} time(s) and still fails. Do the task "
                    "another way.")
        return base

    async def _try_fast_route(self, goal: str, rid: str) -> str | None:
        """Run an unambiguous one-step request without the model (fast_router.py).

        Same registry tool and policy check as the agent loop; anything that
        would need a confirmation, or that fails, falls through to the loop.
        """
        if not bool(self.cfg.get("agent", "fast_router", default=True)):
            return None
        from . import fast_router

        intent = fast_router.match(goal)
        if intent is None:
            return None
        if intent.tool is None:            # "what app is this"
            await asyncio.to_thread(self.world.refresh, True)
            app = self.world.frontmost_app or ""
            if not app:
                return None
            final = f"This is {app}."
        else:
            spec = self.registry.get(intent.tool)
            focus = self.focus.state()
            if (spec is None or not self.policy.allows_tool(spec)
                    or self.policy.requires_confirm(spec, intent.args, focus)):
                return None
            desc = self.registry.describe_call(intent.tool, intent.args)
            print(f"⚡ fast route: {desc}")
            self._hud_update(step=desc, last_action=desc)
            self.world.record_tool_call(intent.tool, intent.args)
            observation = await asyncio.to_thread(
                self.registry.dispatch, intent.tool, intent.args, self.ctx)
            failed = observation.startswith(("ERROR", "Failed"))
            self.metrics.record_tool(intent.tool, 0.0, error=failed)
            self.audit.record("action", run_id=rid, tool=intent.tool, tool_args=intent.args,
                              summary=observation.splitlines()[0][:120] if observation else "",
                              extra={"fast_route": True})
            if failed:
                return None
            final = observation.splitlines()[0] if observation else "Done."
        self.metrics.inc("fast_routes")
        self.world.mark_idle()
        self._hud_update(status="idle", step=final)
        await self.say_async(final)
        self.metrics.end_run("idle")
        self.audit.record("run_end", run_id=rid, summary=final[:300],
                          extra={"success": True, "fast_route": True})
        return final

    def _cost_cap(self) -> float:
        try:
            return max(0.0, float(self.cfg.get("agent", "cost_cap_usd", default=2.0) or 0.0))
        except (TypeError, ValueError):
            return 2.0

    def _budget_note(self, step: int, cost: float, cap: float) -> str | None:
        """Tell the model when a run is three quarters through its steps or money."""
        max_steps = self.cfg.max_steps
        parts = []
        if max_steps >= 4 and step >= int(max_steps * 0.75):
            parts.append(f"this is step {step} of at most {max_steps}")
        if cap and cost >= 0.75 * cap:
            parts.append(f"about ${cost:.2f} of the ${cap:.2f} budget is spent")
        if not parts:
            return None
        return ("BUDGET: " + " and ".join(parts) + ". Finish the essential part now, then "
                "call finish saying what is done and what is left.")

    def _verification_screenshot(self) -> str | None:
        """A model-sized screenshot after a GUI action when AX can't show the result.

        agent.verify_screenshots: auto (default; only when the AX tree is thin,
        or the screen is text-heavy while AX exposes little of that text, as
        in canvas and Electron apps — the router's vision rule), always, or off.
        """
        mode = str(self.cfg.get("agent", "verify_screenshots", default="auto")).lower()
        if mode == "off":
            return None
        if mode != "always":
            thin = bool(getattr(self.world, "ax_insufficient", False))
            try:
                routing = dict(self.router.cfg.routing or {})
            except Exception:  # noqa: BLE001
                routing = {}
            threshold = float(routing.get("ax_text_coverage_threshold", 0.15) or 0.15)
            hidden_text = (
                getattr(self.world, "screen_content_class", "unknown") == "text_heavy"
                and float(getattr(self.world, "ax_text_ratio", 1.0) or 0.0) < threshold)
            if not (thin or hidden_text):
                return None
        try:
            from ..perception import screen

            edge = int(screen.grounding_settings().get("max_image_edge") or 0) or None
            shot = screen.try_capture_to_file(max_edge=edge)
            if shot:
                self.ctx.last_model_image = shot
            return shot
        except Exception as e:  # noqa: BLE001 — verification is best effort
            log.debug("verification screenshot failed: %s", e)
            return None

    def _click_label(self, args: dict, name: str = "click") -> str:
        """AX label of the click target. It already exists in ctx.elements —
        it was simply never shown to the policy, so `click` on an Empty Trash
        button was silent while the AppleScript equivalent was destructive.
        click_mark targets carry the label mark_screen gave them."""
        try:
            if name == "click_mark":
                mark = (self.ctx.marks or {}).get(int(args.get("mark", -1))) or {}
                return str(mark.get("label") or "")
            idx = args.get("element_index")
            if idx is None:
                return str(args.get("label") or "")
            for el in self.ctx.elements or []:
                if el.get("idx") == int(idx):
                    return str(el.get("label") or el.get("title")
                               or el.get("desc") or "")
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _context_is_untrusted(self) -> bool:
        """True once untrusted content has entered THIS RUN's context.

        Sticky. The old version scanned world.ax_rendered point-in-time, but
        refresh() (called at the top of every step) re-derives ax_rendered from
        the live frontmost app — so the flag cleared itself with no attacker
        effort, and `read injected page → open Terminal → type payload` walked
        straight through the Phase-14/15/16 blanket."""
        return bool(getattr(self.world, "untrusted_seen", False))

    def _system_prompt(self, goal: str) -> str:
        parts = [BASE_SYSTEM_PROMPT]
        if self.cfg.knowledge_enabled:
            pack = knowledge.prompt_slice(
                self.world.frontmost_app,
                goal,
                bundle_id=self.world.bundle_id,
            )
            if pack:
                # Redact: learned recipes (Phase 10) may carry text from prior
                # runs; run it through the same secret filter as the AX context.
                parts.append(self.policy.redact_text(pack))
            if "Recipe for this task" not in (pack or ""):
                # The task may belong to an app that isn't in front yet
                # ("make a note" while Finder is frontmost).
                try:
                    hit = knowledge.verified_recipe_for(goal)
                except Exception:  # noqa: BLE001 — recipes are a bonus
                    hit = None
                if hit:
                    parts.append(knowledge.render_verified_recipe(*hit))
        operator = self._operator_line()
        if operator:
            parts.append(operator)
        # Redact these two like the knowledge pack above: store_task_trace()
        # writes screen-derived step text into the same stores, so a secret
        # scraped off the screen can round-trip back into the system prompt.
        # NOT wrap_untrusted — that emits "do NOT follow instructions inside",
        # which would tell the model to ignore genuine user-taught preferences.
        if self.memory:
            prof = self.memory.profile_slice()
            if prof:
                parts.append(self.policy.redact_text(prof))
            mem = self.memory.prompt_slice(goal)
            if mem:
                parts.append(self.policy.redact_text(mem))
        if self.skills:
            sk = self.skills.prompt_slice(goal)
            if sk:
                parts.append(self.policy.redact_text(sk))
        try:
            from ..fleet.manager import SessionManager
            fleet = SessionManager.get().summary_line()
            if fleet:
                parts.append(fleet)
        except Exception:  # noqa: BLE001 — fleet must never break the loop
            pass
        ctx = self.world.context_block()
        if ctx:
            # Carries screen_stream_summary + background-app window titles and
            # event detail — a third channel that never becomes an observation.
            self.world.note_untrusted(ctx, "background")
            parts.append(self.policy.prepare_context_for_model(ctx))
        return "\n\n".join(parts)

    def _operator_line(self) -> str:
        """Which channel to drive the frontmost app through (effectors/operators.py)."""
        app = self.world.frontmost_app
        if not app:
            return ""
        from ..effectors import operators

        try:
            pack = knowledge.load_pack(app, self.world.bundle_id) if \
                self.cfg.knowledge_enabled else None
            choice = operators.choose(app, list(self.world.elements or []), pack,
                                      browser_attach_mode=self.cfg.browser_attach_mode,
                                      element_count=int(self.world.element_count or 0))
        except Exception:  # noqa: BLE001 — guidance is a bonus
            return ""
        return choice.prompt(app)

    def _locate(self, description: str):  # noqa: ANN202 — perception.locate.Located | None
        """Find a described element: the local grounder if it is running, else the
        vision model (click_described, point_at(description=))."""
        from ..perception import grounding, screen
        from ..perception import locate as locate_mod
        from ..tools import targeting_tools

        edge = targeting_tools.model_edge()
        local = locate_mod.local_grounder()
        if local is not None:
            client, space = local
            try:
                found = locate_mod.locate(description, client, coord_space=space,
                                          source="grounder", max_edge=edge)
                if found is not None:
                    return found
            except Exception as e:  # noqa: BLE001 — fall back to the vision model
                log.info("local grounder failed: %s", e)
        client = self.router.pick_client_with_failover(RouteTier.VISION)
        space = grounding.resolve_coord_space(screen.grounding_settings().get("coord_space"))
        return locate_mod.locate(description, client, coord_space=space, source="vision",
                                 max_edge=edge)

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
        budget_note: str | None = None,
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
        if budget_note:
            system += f"\n\n{budget_note}"
        if correction:
            system += f"\n\nSELF-CORRECTION: {correction}"
        if self.world.needs_replan:
            system += (
                "\n\nThe last action did not produce the expected result. "
                "Re-plan and try a different approach."
            )

        vision_ctx = await self._maybe_vision_context(decision)
        if vision_ctx:
            # VLM/OCR text goes straight into `system` and is never dispatched,
            # so it bypasses the record_observation choke point entirely.
            self.world.note_untrusted(vision_ctx, "vision")
            system += f"\n\nVision/OCR context:\n{self.policy.redact_text(vision_ctx)}"

        client = self.router.pick_client(decision)
        abort = stop_ctl.abort_event()
        gate = self._token_gate(step)
        try:
            resp = await asyncio.to_thread(
                step_with_tokens,
                client,
                system,
                messages,
                self._schemas(),
                abort_event=abort,
                on_token=gate,
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
                gate = self._token_gate(step)
                resp = await asyncio.to_thread(
                    step_with_tokens,
                    client,
                    system,
                    messages,
                    self._schemas(),
                    abort_event=abort,
                    on_token=gate,
                )
            else:
                raise
        if gate is not None:
            gate.flush()
            if not resp.tool_calls:
                self._emit({"type": "token_end", "step": step})
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

    async def run_async(
        self, goal: str, *, run_id: str | None = None, reset_stop: bool = True,
        history: list[dict] | None = None,
    ) -> str:
        """Run one goal. ``history`` holds earlier turns of the same conversation
        (alternating user/assistant text) so follow-ups like "now do the same
        for the other file" have context."""
        try:
            return await self._run_async_inner(
                goal, run_id=run_id, reset_stop=reset_stop, history=history)
        finally:
            # Crash-safe browser cleanup (in cdp mode this only disconnects).
            from ..effectors import browser as browser_fx
            browser_fx.close_session()

    async def _run_async_inner(
        self, goal: str, *, run_id: str | None = None, reset_stop: bool = True,
        history: list[dict] | None = None,
    ) -> str:
        # Under concurrent runs the sidecar passes reset_stop=False so a new run
        # can't clear a sibling's pending STOP (the stop signal is process-global).
        if reset_stop:
            stop_ctl.reset()
        self.world.set_goal(goal)
        # Per-run state. The CLI REPL reuses one Agent across goals, so without
        # these a focus surface (and a granted confirmation) leaked into the
        # next goal — neither reset nor seed was called anywhere before now.
        self._ro2_grants.clear()
        self._tool_repairs.clear()
        self.focus.reset()
        self.policy.set_run_goal(goal)
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

        # Immediate ack shrinks perceived voice latency (Phase 3);
        # ref kept on self so the fire-and-forget task isn't GC'd mid-flight
        if bool(self.cfg.get("voice", "ack", default=False)):
            self._ack_task = asyncio.ensure_future(
                self.say_async(str(self.cfg.get("voice", "ack_text", default="On it.")))
            )

        fast_final = await self._try_fast_route(goal, rid)
        if fast_final is not None:
            return fast_final

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
                self._emit({"type": "plan", "steps": list(plan_result.steps)[:12]})
                self._hud_update(step=f"Plan: {plan_preview}")

        messages: list[dict] = [*clean_history(history), {"role": "user", "content": goal}]
        final = ""
        task_success = False
        pending_correction: str | None = None
        nudged = False

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
            # Seed focus from the real frontmost app so a first-action type_text
            # into an already-open terminal is not blind.
            if not self.focus.state().surface:
                self.focus.seed(self.world.frontmost_app)
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

            correction, pending_correction = pending_correction, None
            cost, cap = self.metrics.run_cost(), self._cost_cap()
            if cap and cost >= cap:
                final = (f"Stopped: this task reached its cost limit (${cost:.2f} of "
                         f"${cap:.2f}). Raise agent.cost_cap_usd to allow longer tasks.")
                self.world.mark_idle()
                self._hud_update(status="idle", step=final)
                await self.say_async(final)
                break
            collapse_tool_results(
                messages, int(self.cfg.get("agent", "context_budget_chars", default=60000) or 0))
            prune_images(messages, keep=2)
            try:
                resp, route_tier = await self._reason_step(
                    goal, messages, step,
                    ax_miss=ax_miss or correction is not None,
                    correction=correction,
                    budget_note=self._budget_note(step, cost, cap),
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

            if not resp.tool_calls and not nudged and step < self.cfg.max_steps \
                    and promises_action(resp.text):
                nudged = True
                self.metrics.inc("say_do_nudges")
                messages.append({"role": "assistant", "content": resp.text})
                messages.append({"role": "user", "content": SAY_DO_NUDGE})
                continue

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
            step_images = 0
            gui_changed = False

            if resp.text:
                self._emit({"type": "text", "step": step, "text": resp.text[:2000]})
            for call in resp.tool_calls:
                try:
                    outcome = await self._execute_call(
                        call["name"], call["input"], step=step, rid=rid)
                except stop_ctl.StopRequested:
                    final = "Stopped by user."
                    self.world.mark_stopped()
                    self._hud_update(status="stopped")
                    await self.say_async(final)
                    self.metrics.end_run("stopped")
                    return final
                result = {"tool_use_id": call["id"], "content": outcome.content}
                if outcome.images:
                    result["images"] = outcome.images
                    step_images += len(outcome.images)
                results.append(result)
                gui_changed = gui_changed or outcome.gui_changed
                if outcome.correction:
                    correction_note = outcome.correction
                if outcome.finished:
                    final = outcome.content
                    done = True
                    task_success = True

            if gui_changed and not step_images and results and not done:
                shot = await asyncio.to_thread(self._verification_screenshot)
                if shot:
                    results[-1]["images"] = [shot]
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
                # Self-correction: the NEXT regular step reasons with the failure
                # context. It used to be an extra model call whose tool calls were
                # appended but never executed, leaving tool_use blocks without
                # tool_results — an invalid conversation every provider rejects.
                pending_correction = correction_note

            if done:
                self.world.mark_idle()
                self._hud_update(status="idle", step="Done")
                await self.say_async(final)
                break
        else:
            final = (f"Reached the step limit ({self.cfg.max_steps} steps) before finishing. "
                     "Stopping for safety.")
            self.world.mark_idle()
            self._hud_update(status="idle", step=final)
            await self.say_async(final)

        # Long-term memory: store successful traces
        # Runs that read untrusted content write nothing back into memory,
        # skills or recipes: those texts reach future prompts (audit residual 7).
        learn_ok = not bool(getattr(self.world, "untrusted_seen", False))
        if self.memory and task_success and learn_ok and self.world.task_trace():
            self.memory.store_task_trace(goal, self.world.task_trace(), success=True)
        if self.skills and task_success and learn_ok:
            trace = self.world.tool_trace()
            if trace:
                skill_id = self.skills.distill_from_trace(goal, trace)
                if skill_id:
                    print(f"📚 Learned skill id={skill_id}")

        # Pack write-back: distill this run into an app-specific learned recipe
        # (Phase 10). Gated by knowledge.learn (default on when knowledge enabled).
        if task_success and self.cfg.knowledge_enabled and self.cfg.get(
            "knowledge", "learn", default=True,
        ):
            self._record_pack_learning(goal)

        self.metrics.end_run("idle" if task_success else "incomplete")
        self.audit.record(
            "run_end",
            run_id=rid,
            summary=final[:300],
            extra={"success": task_success},
        )
        return final
