"""Screen memory: opt-in, local-only recall of the text you have looked at.

Off by default (``screen_memory.enabled``). Text only, no images; redacted;
privacy rules that skip anything uncertain; pruned after a retention window;
pause and delete from the menu bar. See docs/SCREEN_MEMORY.md.
"""
from __future__ import annotations

import threading

from .recorder import RecorderSettings, ScreenMemoryRecorder
from .store import ScreenMemoryStore

_lock = threading.Lock()
_recorder: ScreenMemoryRecorder | None = None


def get(raw_config: dict | None = None) -> ScreenMemoryRecorder | None:
    """The process's recorder (created on first use when enabled), else None."""
    global _recorder
    with _lock:
        if _recorder is not None:
            return _recorder
        if raw_config is None:
            from ..core.config import load_config

            raw_config = load_config(validate=False).raw
        settings = RecorderSettings.from_raw(raw_config)
        if not settings.enabled:
            return None
        path = (raw_config.get("screen_memory") or {}).get("db_path")
        _recorder = ScreenMemoryRecorder(settings, ScreenMemoryStore(path))
        return _recorder


def reset() -> None:
    """Test hook."""
    global _recorder
    with _lock:
        if _recorder is not None:
            _recorder.stop()
            _recorder.store.close()
        _recorder = None
