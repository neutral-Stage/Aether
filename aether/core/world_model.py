"""In-memory world model / blackboard (§6.2).

Single source of truth for what the agent perceives and has done.
Phase 2 adds verify-after-act, failure tracking, and AX delta checks.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..perception import accessibility as ax
from ..perception import screen as screen_cap


@dataclass
class HistoryEntry:
    kind: str  # "action" | "observation" | "verify_fail"
    text: str
    ts: float = field(default_factory=time.time)


@dataclass
class VerificationExpectation:
    """Expected state change after an action."""
    app_name: str | None = None
    min_element_count: int | None = None
    contains_text: str | None = None
    element_index_visible: int | None = None
    frontmost_changed: bool = False


@dataclass
class PreActionSnapshot:
    frontmost_app: str
    element_count: int
    ax_rendered: str
    element_indices: set[int]


class WorldModel:
    """Blackboard: latest percept, goal, plan, bounded history."""

    def __init__(self, history_limit: int = 40, ax_cache_ttl_ms: float = 200):
        self.history_limit = history_limit
        self.ax_cache_ttl_ms = ax_cache_ttl_ms
        self._last_refresh_ts: float = 0.0
        self._cached_percept: dict[str, Any] | None = None
        self.frontmost_app: str = ""
        self.bundle_id: str = ""
        self.elements: list[dict] = []
        self.ax_rendered: str = ""
        self.element_count: int = 0
        self.last_screenshot: str | None = None
        self.transcript: str = ""
        self.active_goal: str = ""
        self.plan: list[str] = []
        self.current_step: str = ""
        self.last_action: str = ""
        self.status: str = "idle"  # idle | working | stopped
        self.step_failure_count: int = 0
        self.ax_insufficient: bool = False
        self.needs_replan: bool = False
        self.is_novel_goal: bool = True
        self._history: deque[HistoryEntry] = deque(maxlen=history_limit)
        self._pre_action: PreActionSnapshot | None = None
        self._pending_expectation: VerificationExpectation | None = None
        self._task_steps: list[str] = []
        self._tool_trace: list[dict[str, Any]] = []
        self.screen_stream_summary: str = ""
        self.screen_content_class: str = "unknown"
        self.text_heavy_score: float = 0.0
        self.ax_text_ratio: float = 1.0  # ax_text_ratio: reserved — not yet computed in the perception loop (AX-present-but-wrong stays dormant until then)

    def set_goal(self, goal: str) -> None:
        self.active_goal = goal
        self.plan = []
        self.status = "working"
        self.step_failure_count = 0
        self.needs_replan = False
        self.is_novel_goal = True
        self._task_steps = []
        self._tool_trace = []
        self.ax_insufficient = False

    def set_plan(self, steps: list[str]) -> None:
        self.plan = list(steps)
        self.is_novel_goal = False

    def set_transcript(self, text: str) -> None:
        self.transcript = text

    def set_screen_stream(self, summary: str) -> None:
        """Update continuous screen percept from Swift SCStream."""
        self.screen_stream_summary = summary or ""

    def refresh(self, force: bool = False) -> dict[str, Any]:
        """Perceive: refresh AX tree and system state (TTL-cached)."""
        now = time.time()
        if (
            not force
            and self._cached_percept is not None
            and (now - self._last_refresh_ts) * 1000 < self.ax_cache_ttl_ms
        ):
            try:
                from ..core.metrics import MetricsCollector
                MetricsCollector.get().inc("ax_cache_hits")
            except Exception:
                pass
            return self._cached_percept

        t0 = time.time()
        data = ax.screen_context()
        elapsed_ms = (time.time() - t0) * 1000
        try:
            from ..core.metrics import MetricsCollector
            m = MetricsCollector.get()
            m.observe("ax_refresh_ms", elapsed_ms)
            m.warn_if_slow("ax_refresh_ms", elapsed_ms)
        except Exception:
            pass

        self.frontmost_app = data.get("frontmost_app", "")
        self.bundle_id = data.get("bundle_id", "")
        self.elements = data.get("elements", [])
        self.ax_rendered = data.get("rendered", "")
        self.element_count = int(data.get("element_count", 0))
        self.ax_insufficient = self.element_count < 3
        self._cached_percept = data
        self._last_refresh_ts = now
        return data

    def snapshot(self) -> dict[str, Any]:
        """Compact state for LLM context / HUD."""
        recent = [f"[{h.kind}] {h.text}" for h in list(self._history)[-12:]]
        return {
            "goal": self.active_goal,
            "plan": self.plan,
            "current_step": self.current_step,
            "frontmost_app": self.frontmost_app,
            "element_count": self.element_count,
            "screen_summary": self.ax_rendered,
            "last_screenshot": self.last_screenshot,
            "transcript": self.transcript,
            "last_action": self.last_action,
            "status": self.status,
            "step_failure_count": self.step_failure_count,
            "ax_insufficient": self.ax_insufficient,
            "needs_replan": self.needs_replan,
            "recent_history": recent,
        }

    def context_block(self) -> str:
        """Text block injected into reasoning when helpful."""
        snap = self.snapshot()
        lines = [
            f"Frontmost app: {snap['frontmost_app']}",
            f"Goal: {snap['goal']}",
            f"AX elements: {snap['element_count']}",
        ]
        if snap["step_failure_count"]:
            lines.append(f"Recent failures: {snap['step_failure_count']}")
        if snap["needs_replan"]:
            lines.append("VERIFY FAILED — replan or try a different approach.")
        if snap["plan"]:
            lines.append("Plan: " + " → ".join(snap["plan"][:6]))
        if snap["last_action"]:
            lines.append(f"Last action: {snap['last_action']}")
        if self.screen_stream_summary:
            lines.append(f"Screen stream: {self.screen_stream_summary}")
        if snap["recent_history"]:
            lines.append("Recent:\n" + "\n".join(snap["recent_history"][-6:]))
        return "\n".join(lines)

    def record_action(self, description: str) -> None:
        self.last_action = description
        self.current_step = description
        self._history.append(HistoryEntry("action", description))
        self._task_steps.append(description)

    def record_tool_call(self, name: str, args: dict[str, Any]) -> None:
        """Structured trace for skill memory distillation."""
        self._tool_trace.append({"tool": name, "args": dict(args)})

    def record_observation(self, text: str) -> None:
        self._history.append(HistoryEntry("observation", text[:500]))

    def capture_screenshot(self) -> str | None:
        path = screen_cap.try_capture_to_file()
        if path:
            self.last_screenshot = path
        return path

    def begin_action_verification(
        self,
        expectation: VerificationExpectation | None = None,
    ) -> None:
        """Snapshot state before an action for delta verification."""
        indices = {el.get("idx") for el in self.elements if "idx" in el}
        self._pre_action = PreActionSnapshot(
            frontmost_app=self.frontmost_app,
            element_count=self.element_count,
            ax_rendered=self.ax_rendered,
            element_indices=indices,
        )
        self._pending_expectation = expectation

    def verify(self, expected: dict | None, observation: str) -> bool:
        """Verify-after-act: compare AX/state delta after an action."""
        ok = True
        reasons: list[str] = []

        # Legacy string contains check
        if expected and expected.get("contains"):
            if expected["contains"].lower() not in (observation or "").lower():
                ok = False
                reasons.append(f"missing text '{expected['contains']}'")

        exp = self._pending_expectation
        if exp is not None:
            self.refresh()
            if exp.app_name and exp.app_name.lower() not in self.frontmost_app.lower():
                ok = False
                reasons.append(f"expected app '{exp.app_name}', got '{self.frontmost_app}'")
            if exp.min_element_count is not None and self.element_count < exp.min_element_count:
                ok = False
                reasons.append(
                    f"element count {self.element_count} < {exp.min_element_count}"
                )
            if exp.contains_text:
                hay = (self.ax_rendered + observation).lower()
                if exp.contains_text.lower() not in hay:
                    ok = False
                    reasons.append(f"AX missing '{exp.contains_text}'")
            if exp.element_index_visible is not None:
                visible = {el.get("idx") for el in self.elements}
                if exp.element_index_visible not in visible:
                    ok = False
                    reasons.append(f"element [{exp.element_index_visible}] not visible")
            if exp.frontmost_changed and self._pre_action:
                if self.frontmost_app == self._pre_action.frontmost_app:
                    ok = False
                    reasons.append("frontmost app did not change")

        # AX tree delta heuristic when no explicit expectation
        if ok and self._pre_action and exp is None:
            delta = abs(self.element_count - self._pre_action.element_count)
            if "error" in (observation or "").lower():
                ok = False
                reasons.append("observation contains error")
            elif delta == 0 and self.ax_rendered == self._pre_action.ax_rendered:
                # No visible change — soft fail for non-read tools
                if self.last_action and "get_screen" not in self.last_action.lower():
                    ok = False
                    reasons.append("no AX delta after action")

        self._pre_action = None
        self._pending_expectation = None

        if not ok:
            self.step_failure_count += 1
            self.needs_replan = True
            msg = "; ".join(reasons) or "verification failed"
            self._history.append(HistoryEntry("verify_fail", msg))
            return False

        self.needs_replan = False
        return True

    def clear_replan(self) -> None:
        self.needs_replan = False

    def task_trace(self) -> list[str]:
        return list(self._task_steps)

    def tool_trace(self) -> list[dict[str, Any]]:
        return list(self._tool_trace)

    def mark_stopped(self) -> None:
        self.status = "stopped"

    def mark_idle(self) -> None:
        self.status = "idle"
