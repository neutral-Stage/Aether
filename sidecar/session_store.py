"""Conversations: runs grouped into sessions so follow-ups keep context.

Each turn stores the user's request, Aether's final answer and a short list
of the actions it took. history() turns that into alternating user/assistant
messages for Agent.run_async(history=...). Tool transcripts are not kept:
they are large, and the next run looks at the screen afresh anyway.

SQLite at <data_dir>/sessions.db (thread-safe; one connection per call).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from aether.core.paths import data_dir

# Override hook (tests); None → <data_dir>/sessions.db.
_PATH: Path | None = None
_lock = threading.Lock()
MAX_ACTIONS_PER_TURN = 20

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL, goal TEXT NOT NULL, result TEXT NOT NULL,
    actions TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'idle',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_by_session ON turns(session_id, id);
"""


def _path() -> Path:
    return _PATH if _PATH is not None else data_dir() / "sessions.db"


def _connect() -> sqlite3.Connection:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def _title_for(goal: str) -> str:
    title = " ".join(goal.split())
    return title if len(title) <= 60 else title[:57] + "…"


def create_session(first_goal: str = "") -> str:
    sid = uuid.uuid4().hex[:12]
    now = time.time()
    with _db() as conn:
        conn.execute("INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?,?,?,?)",
                     (sid, _title_for(first_goal) or "New conversation", now, now))
    return sid


def exists(session_id: str) -> bool:
    with _db() as conn:
        return conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone() \
            is not None


def add_turn(session_id: str, run_id: str, goal: str, result: str,
             actions: list[str] | None = None, status: str = "idle") -> None:
    now = time.time()
    acts = [str(a)[:120] for a in (actions or [])][-MAX_ACTIONS_PER_TURN:]
    with _db() as conn:
        conn.execute(
            "INSERT INTO turns (session_id, run_id, goal, result, actions, status, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (session_id, run_id, goal, result or "", json.dumps(acts), status, now))
        conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))


def history(session_id: str, max_turns: int = 10) -> list[dict[str, str]]:
    """Earlier turns as alternating user/assistant messages (oldest first)."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT goal, result, actions, status FROM turns WHERE session_id=? "
            "ORDER BY id DESC LIMIT ?", (session_id, max_turns)).fetchall()
    out: list[dict[str, str]] = []
    for row in reversed(rows):
        actions = json.loads(row["actions"] or "[]")
        reply = row["result"] or "(no answer)"
        if actions:
            reply += "\n(Actions taken: " + "; ".join(actions) + ")"
        if row["status"] not in ("idle", "done"):
            reply += f"\n(This request ended as: {row['status']})"
        out += [{"role": "user", "content": row["goal"]},
                {"role": "assistant", "content": reply}]
    return out


def list_sessions(limit: int = 50) -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT s.id, s.title, s.created_at, s.updated_at, COUNT(t.id) AS turns "
            "FROM sessions s LEFT JOIN turns t ON t.session_id = s.id "
            "GROUP BY s.id ORDER BY s.updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_session(session_id: str) -> dict[str, Any] | None:
    with _db() as conn:
        s = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if s is None:
            return None
        turns = conn.execute(
            "SELECT run_id, goal, result, actions, status, created_at FROM turns "
            "WHERE session_id=? ORDER BY id", (session_id,)).fetchall()
    return {**dict(s), "turns": [{**dict(t), "actions": json.loads(t["actions"] or "[]")}
                                 for t in turns]}


def delete_session(session_id: str) -> bool:
    with _db() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        return cur.rowcount > 0
