"""Meetings and their transcripts (SQLite + full-text index, readable only by you)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ..core.paths import resolve_data_path
from ..memory.hybrid import fts_query

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, app TEXT NOT NULL, started REAL NOT NULL,
    ended REAL, summary TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY, meeting_id TEXT NOT NULL, ts REAL NOT NULL,
    channel TEXT NOT NULL, text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS segments_meeting ON segments(meeting_id, ts);
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
    text, content='segments', content_rowid='id', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS segments_ai AFTER INSERT ON segments BEGIN
  INSERT INTO segments_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS segments_ad AFTER DELETE ON segments BEGIN
  INSERT INTO segments_fts(segments_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
"""
CHANNELS = ("them", "me")


class MeetingStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = resolve_data_path(path, "meetings.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        os.chmod(self.path, 0o600)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def create(self, title: str, app: str, started: float | None = None) -> str:
        mid = uuid.uuid4().hex[:12]
        with self._lock:
            self._conn.execute("INSERT INTO meetings (id, title, app, started) VALUES (?,?,?,?)",
                               (mid, title.strip()[:120] or "Meeting", app[:80],
                                time.time() if started is None else started))
            self._conn.commit()
        return mid

    def meeting(self, mid: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone()
        if r is None:
            return None
        out = dict(r)
        try:
            out["notes"] = json.loads(out.get("notes") or "{}")
        except ValueError:
            out["notes"] = {}
        return out

    def add_segment(self, mid: str, channel: str, text: str, ts: float) -> int:
        text = " ".join(text.split())
        if channel not in CHANNELS or not text:
            return 0
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO segments (meeting_id, ts, channel, text) VALUES (?,?,?,?)",
                (mid, ts, channel, text))
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def segments(self, mid: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, channel, text FROM segments WHERE meeting_id=? ORDER BY ts, id",
                (mid,)).fetchall()
        return [dict(r) for r in rows]

    def end(self, mid: str, ended: float | None = None) -> None:
        with self._lock:
            self._conn.execute("UPDATE meetings SET ended=? WHERE id=? AND ended IS NULL",
                               (time.time() if ended is None else ended, mid))
            self._conn.commit()

    def set_notes(self, mid: str, summary: str, notes: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute("UPDATE meetings SET summary=?, notes=? WHERE id=?",
                               (summary, json.dumps(notes), mid))
            self._conn.commit()

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.id, m.title, m.app, m.started, m.ended, m.summary, "
                "(SELECT COUNT(*) FROM segments s WHERE s.meeting_id = m.id) AS segments "
                "FROM meetings m ORDER BY m.started DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Meetings whose transcript matches, newest first, with a matching line."""
        match = fts_query(query)
        if not match:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.meeting_id, s.ts, s.channel, "
                "snippet(segments_fts, 0, '[', ']', ' … ', 20) AS snip "
                "FROM segments_fts JOIN segments s ON s.id = segments_fts.rowid "
                "WHERE segments_fts MATCH ? ORDER BY bm25(segments_fts) LIMIT 200",
                (match,)).fetchall()
        seen: dict[str, dict[str, Any]] = {}
        for r in rows:
            if r["meeting_id"] not in seen:
                seen[r["meeting_id"]] = {"id": r["meeting_id"], "ts": r["ts"],
                                         "channel": r["channel"], "snippet": r["snip"]}
        out = []
        for mid, hit in seen.items():
            m = self.meeting(mid)
            if m:
                out.append({**hit, "title": m["title"], "started": m["started"]})
        out.sort(key=lambda h: -h["started"])
        return out[:limit]

    def delete(self, mid: str) -> bool:
        with self._lock:
            self._conn.execute("DELETE FROM segments WHERE meeting_id=?", (mid,))
            cur = self._conn.execute("DELETE FROM meetings WHERE id=?", (mid,))
            self._conn.commit()
            return cur.rowcount > 0

    def close(self) -> None:
        self._conn.close()
