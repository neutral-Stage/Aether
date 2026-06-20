"""In-memory screen percept channel from Swift SCStream (Phase 8)."""
from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_latest: dict[str, Any] = {}
_history: list[dict[str, Any]] = []
_max_history = 20


def update(payload: dict[str, Any]) -> None:
    with _lock:
        entry = {**payload, "received_at": time.time()}
        _latest.clear()
        _latest.update(entry)
        _history.append(entry)
        if len(_history) > _max_history:
            del _history[: len(_history) - _max_history]


def latest() -> dict[str, Any]:
    with _lock:
        return dict(_latest)


def summary_for_context() -> str:
    with _lock:
        if not _latest:
            return ""
        parts = [
            f"Swift screen stream (fps={_latest.get('fps', '?')})",
            f"display: {_latest.get('width', '?')}x{_latest.get('height', '?')}",
        ]
        if _latest.get("frontmost_app"):
            parts.append(f"frontmost: {_latest['frontmost_app']}")
        if _latest.get("note"):
            parts.append(str(_latest["note"]))
        return " | ".join(parts)
