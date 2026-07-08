"""Durable, append-only event store for run/graph/fleet state (Phase 9).

Nothing else in the sidecar survives a restart — runs, graphs, and fleet
sessions are in-memory singletons. This writes one JSON line per state
transition to ``data/state_events.jsonl`` and can fold the log back into a
last-known-status map, so a startup reconcile pass can flip orphaned
``running`` records to ``interrupted`` instead of silently losing them.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_PATH = _ROOT / "data" / "state_events.jsonl"
_lock = threading.Lock()

TERMINAL = frozenset({"idle", "done", "failed", "stopped", "timeout",
                      "interrupted", "blocked", "skipped", "error"})


def _store_path() -> Path:
    return _PATH


def append(kind: str, id: str, status: str, **fields: Any) -> None:
    """Record one state transition. Best-effort — never raises into callers."""
    event = {"ts": time.time(), "kind": kind, "id": id, "status": status, **fields}
    try:
        path = _store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, default=str)
        with _lock, path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass  # durability is best-effort; a full disk must not break the agent


def replay() -> dict[str, dict[str, Any]]:
    """Fold the log into {(kind,id): last_event}. Last line wins per id."""
    out: dict[str, dict[str, Any]] = {}
    path = _store_path()
    if not path.exists():
        return out
    try:
        with _lock, path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = f"{ev.get('kind')}:{ev.get('id')}"
                out[key] = ev
    except OSError:
        return out
    return out


def non_terminal() -> list[dict[str, Any]]:
    """Records whose last-known status is non-terminal — the reconcile targets."""
    return [ev for ev in replay().values() if ev.get("status") not in TERMINAL]
