"""A running guide: point at a step, wait for the user to do it, advance.

Events (sent through ``emit``):
  guide_step     {guide_id, index, total, say, done_when, target | None}
  guide_done     {guide_id, status: done | stopped, total}
  guide_handoff  {guide_id, goal, remaining}  — "do it for me"
  run_request    {goal}  — asks the app to start an agent run for the rest

Controls: next, back, repeat, skip, stop, do_it. A step that isn't done
after ``step_timeout`` seconds pauses and waits for a control.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from typing import Any

from ..core import stop as stop_ctl
from ..effectors import targeting
from ..perception.pointing import Target
from .detect import ClickWatcher, Detector
from .plan import GuideStep

ACTIONS = ("next", "back", "repeat", "skip", "stop", "do_it")


def resolve_target(step: GuideStep) -> Target | None:
    """The step's control on a fresh accessibility tree, or None."""
    if not step.target:
        return None
    for app in ([step.app, None] if step.app else [None]):
        try:
            match, _, _ = targeting.find(step.target, step.role or None, app)
        except Exception:  # noqa: BLE001 — app not running yet
            continue
        if match is not None:
            el = match.element
            return Target("point", el.x + el.w / 2.0, el.y + el.h / 2.0,
                          step.target, float(el.w), float(el.h), source="ax")
    return None


class GuideSession:
    def __init__(self, goal: str, steps: list[GuideStep], *,
                 emit: Callable[[dict], Any],
                 detector_factory: Callable[[GuideStep], Detector] = Detector,
                 resolve: Callable[[GuideStep], Target | None] = resolve_target,
                 clicks: ClickWatcher | None = None,
                 poll: float = 0.3, step_timeout: float = 300.0) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.goal = goal
        self.steps = steps
        self.index = 0
        self.status = "ready"
        self._emit = emit
        self._detector_factory = detector_factory
        self._resolve = resolve
        self._clicks = clicks
        self._poll = poll
        self._step_timeout = step_timeout
        self._commands: asyncio.Queue[str] = asyncio.Queue()

    def state(self) -> dict[str, Any]:
        return {"guide_id": self.id, "goal": self.goal, "status": self.status,
                "index": self.index, "total": len(self.steps),
                "steps": [s.as_dict() for s in self.steps]}

    def control(self, action: str) -> bool:
        if action not in ACTIONS or self.status not in ("running", "paused"):
            return False
        self._commands.put_nowait(action)
        return True

    async def _emit_async(self, event: dict) -> None:
        res = self._emit({**event, "guide_id": self.id})
        if asyncio.iscoroutine(res):
            await res

    async def run(self) -> str:
        self.status = "running"
        try:
            while 0 <= self.index < len(self.steps):
                step = self.steps[self.index]
                target = await asyncio.to_thread(self._resolve, step)
                await self._emit_async({
                    "type": "guide_step", "index": self.index, "total": len(self.steps),
                    "say": step.say, "done_when": step.done_when,
                    "target": target.as_dict() if target else None})
                outcome = await self._wait(step)
                if outcome in ("done", "next", "skip"):
                    self.index += 1
                elif outcome == "back":
                    self.index = max(0, self.index - 1)
                elif outcome == "repeat":
                    continue
                elif outcome == "stop":
                    self.status = "stopped"
                    await self._emit_async({"type": "guide_done", "status": "stopped",
                                            "total": len(self.steps)})
                    return self.status
                elif outcome == "do_it":
                    return await self._hand_off()
            self.status = "done"
            await self._emit_async({"type": "guide_done", "status": "done",
                                    "total": len(self.steps)})
            return self.status
        finally:
            if self._clicks is not None:
                self._clicks.stop()

    async def _hand_off(self) -> str:
        self.status = "handoff"
        remaining = [s.say for s in self.steps[self.index:]]
        await self._emit_async({"type": "guide_handoff", "goal": self.goal,
                                "remaining": remaining})
        goal = (f"Finish this for the user: {self.goal}. They were being guided and "
                "handed over at: " + " Then ".join(remaining))
        await self._emit_async({"type": "run_request", "goal": goal[:1500]})
        return self.status

    async def _next_command(self, timeout: float) -> str | None:
        try:
            return await asyncio.wait_for(self._commands.get(), timeout)
        except asyncio.TimeoutError:
            return None

    async def _wait(self, step: GuideStep) -> str:
        detector = self._detector_factory(step)
        before = await asyncio.to_thread(detector.snapshot)
        started = since = time.monotonic()
        clicks_seen = bool(self._clicks and self._clicks.running)
        while True:
            if stop_ctl.is_set():
                return "stop"
            cmd = await self._next_command(self._poll)
            if cmd:
                return cmd
            if time.monotonic() - started > self._step_timeout:
                self.status = "paused"
                while True:                       # wait for the user to say what to do
                    if stop_ctl.is_set():
                        return "stop"
                    cmd = await self._next_command(0.5)
                    if cmd:
                        self.status = "running"
                        return cmd
            now = await asyncio.to_thread(detector.snapshot)
            clicks = self._clicks.clicks_since(since) if clicks_seen else []
            if detector.done(before, now, clicks, clicks_seen=clicks_seen):
                return "done"
