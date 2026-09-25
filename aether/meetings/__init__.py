"""Meeting notes: a transcript of one meeting app and your microphone, then a
summary and action items. Started only by the user, after a consent prompt.
Audio is transcribed and discarded; see docs/MEETINGS.md."""
from __future__ import annotations

import threading

from .notes import SUMMARY_SYSTEM, parse_notes, render_notes, transcript_for_model
from .store import MeetingStore

__all__ = ["SUMMARY_SYSTEM", "MeetingStore", "get_store", "parse_notes", "render_notes",
           "reset", "transcript_for_model"]

_lock = threading.Lock()
_store: MeetingStore | None = None


def get_store(raw_config: dict | None = None) -> MeetingStore:
    """The process's meeting store (meetings.db_path, else <data dir>/meetings.db)."""
    global _store
    with _lock:
        if _store is None:
            if raw_config is None:
                from ..core.config import load_config

                raw_config = load_config(validate=False).raw
            _store = MeetingStore((raw_config.get("meetings") or {}).get("db_path"))
        return _store


def reset() -> None:
    """Test hook."""
    global _store
    with _lock:
        if _store is not None:
            _store.close()
        _store = None
