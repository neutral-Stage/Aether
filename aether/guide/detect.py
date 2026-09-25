"""Tell when the user has done a guide step, so the guide advances by itself.

Two signals, best first:
1. Clicks: a listen-only event tap records mouse-down positions (needs Input
   Monitoring); a click inside the target's frame completes a click step.
2. State: accessibility snapshots polled every few hundred milliseconds —
   the front app, the focused element and its value, the front app's window
   titles, and the target element itself (value, presence, frame). A step
   completes when the change it asks for shows up; without a click tap, a
   change around the target stands in for the click.

All readers are injectable, so the logic is tested without a Mac.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..effectors import targeting
from ..perception import accessibility as ax
from .plan import GuideStep

CLICK_PAD_PT = 6.0


@dataclass(frozen=True)
class Snapshot:
    frontmost: str = ""
    focused_role: str = ""
    focused_title: str = ""
    focused_value: str = ""
    windows: tuple[str, ...] = ()
    target_found: bool = False
    target_frame: tuple[float, float, float, float] | None = None
    target_value: str = ""


def _front_windows() -> tuple[str, ...]:
    front = ax.frontmost_app().get("name", "")
    for row in ax.list_windows():
        if row.get("app") == front:
            return tuple(str(w) for w in row.get("windows") or [])
    return ()


class Detector:
    def __init__(self, step: GuideStep, *,
                 frontmost: Callable[[], str] | None = None,
                 focused: Callable[[], dict] | None = None,
                 windows: Callable[[], tuple[str, ...]] | None = None,
                 find: Callable[..., Any] | None = None) -> None:
        self.step = step
        self._frontmost = frontmost or (lambda: str(ax.frontmost_app().get("name", "")))
        self._focused = focused or ax.focused_summary
        self._windows = windows or _front_windows
        self._find = find or targeting.find

    def _target(self) -> tuple[bool, tuple | None, str]:
        if not self.step.target:
            return False, None, ""
        try:
            match, _, _ = self._find(self.step.target, self.step.role or None,
                                     self.step.app or None)
        except Exception:  # noqa: BLE001 — app not running yet, AX hiccup
            try:
                match, _, _ = self._find(self.step.target, self.step.role or None, None)
            except Exception:  # noqa: BLE001
                return False, None, ""
        if match is None:
            return False, None, ""
        el = match.element
        return True, (el.x, el.y, el.w, el.h), str(el.value or "")

    def snapshot(self) -> Snapshot:
        foc = self._focused() or {}
        found, frame, value = self._target()
        return Snapshot(self._frontmost(), str(foc.get("role", "")), str(foc.get("title", "")),
                        str(foc.get("value", "")), tuple(self._windows()), found, frame, value)

    # -- the rules --------------------------------------------------------------------------------

    @staticmethod
    def _inside(frame: tuple | None, x: float, y: float) -> bool:
        if not frame:
            return False
        fx, fy, fw, fh = frame
        return (fx - CLICK_PAD_PT <= x <= fx + fw + CLICK_PAD_PT
                and fy - CLICK_PAD_PT <= y <= fy + fh + CLICK_PAD_PT)

    def _is_target(self, title: str) -> bool:
        """Does a (non-empty) element title name this step's target?"""
        return bool(self.step.target and title.strip()) and targeting.label_consistent(
            title, self.step.target)

    def _changed_near_target(self, before: Snapshot, now: Snapshot) -> bool:
        focused_target = self._is_target(now.focused_title)
        return (now.target_value != before.target_value
                or (before.target_found and not now.target_found)
                or (focused_target and now.focused_title != before.focused_title)
                or now.windows != before.windows
                or now.frontmost != before.frontmost)

    def done(self, before: Snapshot, now: Snapshot, clicks: list[tuple[float, float]],
             clicks_seen: bool = False) -> bool:
        """Has the user done this step? ``clicks_seen`` says a click tap is running."""
        kind, expect = self.step.done_when, self.step.expect.casefold()
        if kind == "app":
            return bool(expect) and expect in now.frontmost.casefold()
        if kind == "window":
            return bool(expect) and any(expect in w.casefold() for w in now.windows)
        if kind == "focus":
            return self._is_target(now.focused_title)
        if kind == "type":
            value = now.target_value if now.target_found else now.focused_value
            old = before.target_value if before.target_found else before.focused_value
            if expect:
                return expect in value.casefold()
            return value != old and bool(value)
        if kind in ("click", "menu"):
            frame = before.target_frame or now.target_frame
            if clicks and any(self._inside(frame, x, y) for x, y in clicks):
                return True
            if clicks_seen and frame:
                return False                       # we see clicks; none hit the target yet
            return self._changed_near_target(before, now)
        # any
        return now != before


class ClickWatcher:
    """Listen-only tap on mouse-down events. start() is False without permission."""

    def __init__(self) -> None:
        self._clicks: deque = deque(maxlen=64)
        self._lock = threading.Lock()
        self._loop: Any = None
        self._tap: Any = None
        self.running = False

    def start(self) -> bool:
        try:
            import Quartz
        except Exception:  # noqa: BLE001 — not macOS
            return False
        ready = threading.Event()

        def callback(proxy, etype, event, refcon):  # noqa: ANN001, ANN202, ARG001
            if etype in (Quartz.kCGEventTapDisabledByTimeout,
                         Quartz.kCGEventTapDisabledByUserInput):
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
                return event
            loc = Quartz.CGEventGetLocation(event)
            with self._lock:
                self._clicks.append((float(loc.x), float(loc.y), time.monotonic()))
            return event

        def run() -> None:
            mask = (Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDown)
                    | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseDown))
            tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                          Quartz.kCGEventTapOptionListenOnly, mask, callback, None)
            if tap is None:
                ready.set()
                return
            self._tap = tap
            source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            self._loop = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(self._loop, source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            self.running = True
            ready.set()
            Quartz.CFRunLoopRun()
            self.running = False

        threading.Thread(target=run, name="aether-guide-clicks", daemon=True).start()
        ready.wait(2.0)
        return self.running

    def clicks_since(self, t: float) -> list[tuple[float, float]]:
        with self._lock:
            return [(x, y) for x, y, ts in self._clicks if ts >= t]

    def stop(self) -> None:
        try:
            import Quartz

            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, False)
            if self._loop is not None:
                Quartz.CFRunLoopStop(self._loop)
        except Exception:  # noqa: BLE001
            pass
        self.running = False
