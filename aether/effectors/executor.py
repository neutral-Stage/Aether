"""Concurrent-action arbitration (Phase 4).

Two invariants when multiple callers act at once (main agent loop + fleet
agents via the MCP server):

1. HID is a single shared device — CGEvent clicks/typing and focus changes
   hold ``HID_LOCK``. AX-handle actions on background apps don't need it and
   may run concurrently.
2. Never steal focus while the user is typing/mousing — ``focused_action``
   requires ``user_idle_seconds() >= idle_threshold`` before focus-juggling,
   and restores the previous frontmost app after.

# ponytail: global HID lock + idle guard; per-app action queues if concurrent
# same-app AX writers ever contend.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

HID_LOCK = threading.RLock()

IDLE_THRESHOLD_SEC = 2.0

try:
    import Quartz
    _QUARTZ_OK = True
except Exception:  # pragma: no cover - non-macOS
    _QUARTZ_OK = False


def user_idle_seconds() -> float:
    """Seconds since the user's last keyboard/mouse input (0.0 if unknown)."""
    if not _QUARTZ_OK:
        return 0.0
    try:
        return float(Quartz.CGEventSourceSecondsSinceLastEventType(
            Quartz.kCGEventSourceStateHIDSystemState,
            Quartz.kCGAnyInputEventType,
        ))
    except Exception:
        return 0.0


def _activate(pid: int) -> bool:
    try:
        from AppKit import NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            return False
        app.activateWithOptions_(0)
        return True
    except Exception:
        return False


def focused_action(
    target_pid: int,
    fn: Callable[[], Any],
    idle_threshold: float = IDLE_THRESHOLD_SEC,
    settle_sec: float = 0.4,
) -> Any:
    """Run fn with target_pid frontmost, then restore the previous app.

    Raises RuntimeError if the user is active (don't steal focus mid-keystroke).
    """
    idle = user_idle_seconds()
    if idle < idle_threshold:
        raise RuntimeError(
            f"user is active (idle {idle:.1f}s < {idle_threshold}s) — "
            "not stealing focus; retry shortly or use an AX-handle action"
        )
    from ..perception import accessibility as ax
    prev = ax.frontmost_app()
    with HID_LOCK:
        if not _activate(target_pid):
            raise RuntimeError(f"could not activate pid {target_pid}")
        time.sleep(settle_sec)
        try:
            return fn()
        finally:
            if prev.get("pid", -1) > 0 and prev["pid"] != target_pid:
                _activate(prev["pid"])
