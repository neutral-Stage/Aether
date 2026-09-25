"""Screen memory store: text seen on screen, by time, app and window (SQLite + FTS5)."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.paths import resolve_data_path
from ..memory.hybrid import fts_query

_SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, app TEXT NOT NULL, bundle_id TEXT NOT NULL,
    window TEXT NOT NULL, text TEXT NOT NULL, source TEXT NOT NULL, digest TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS captures_ts ON captures(ts);
CREATE VIRTUAL TABLE IF NOT EXISTS captures_fts USING fts5(
    app, window, text, content='captures', content_rowid='id',
    tokenize='porter unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS captures_ai AFTER INSERT ON captures BEGIN
  INSERT INTO captures_fts(rowid, app, window, text) VALUES (new.id, new.app, new.window, new.text);
END;
CREATE TRIGGER IF NOT EXISTS captures_ad AFTER DELETE ON captures BEGIN
  INSERT INTO captures_fts(captures_fts, rowid, app, window, text)
  VALUES ('delete', old.id, old.app, old.window, old.text);
END;
"""
DEDUPE_WINDOW_S = 3600


@dataclass
class Capture:
    id: int
    ts: float
    app: str
    bundle_id: str
    window: str
    text: str
    source: str
    snippet: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "ts": self.ts, "time": time.strftime("%Y-%m-%d %H:%M",
                                                                     time.localtime(self.ts)),
                "app": self.app, "window": self.window, "source": self.source,
                "snippet": self.snippet or self.text[:240]}


class ScreenMemoryStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = resolve_data_path(path, "screen_memory.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.path.touch(exist_ok=True)
        # Only this user may read it (SQLite gives its journal the same mode).
        os.chmod(self.path, 0o600)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def add(self, *, app: str, bundle_id: str, window: str, text: str, source: str,
            ts: float | None = None) -> int:
        """Store a capture; 0 when the same window showed the same text within the hour."""
        ts = time.time() if ts is None else ts
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
        with self._lock:
            dup = self._conn.execute(
                "SELECT 1 FROM captures WHERE bundle_id=? AND window=? AND digest=? AND ts>?",
                (bundle_id, window, digest, ts - DEDUPE_WINDOW_S)).fetchone()
            if dup:
                return 0
            cur = self._conn.execute(
                "INSERT INTO captures (ts, app, bundle_id, window, text, source, digest) "
                "VALUES (?,?,?,?,?,?,?)", (ts, app, bundle_id, window, text, source, digest))
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def search(self, query: str, *, since: float | None = None, until: float | None = None,
               app: str = "", limit: int = 8) -> list[Capture]:
        where, params = self._range(since, until, app)
        match = fts_query(query)
        with self._lock:
            if match:
                rows = self._conn.execute(
                    "SELECT c.*, snippet(captures_fts, 2, '[', ']', ' … ', 24) AS snip "
                    "FROM captures_fts JOIN captures c ON c.id = captures_fts.rowid "
                    f"WHERE captures_fts MATCH ? {where} "
                    "ORDER BY bm25(captures_fts), c.ts DESC LIMIT ?",
                    (match, *params, limit)).fetchall()
            else:
                rows = self._conn.execute(
                    f"SELECT c.*, '' AS snip FROM captures c WHERE 1=1 {where} "
                    "ORDER BY c.ts DESC LIMIT ?", (*params, limit)).fetchall()
        return [Capture(r["id"], r["ts"], r["app"], r["bundle_id"], r["window"], r["text"],
                        r["source"], r["snip"]) for r in rows]

    def get(self, capture_id: int) -> Capture | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM captures WHERE id=?", (capture_id,)).fetchone()
        return Capture(r["id"], r["ts"], r["app"], r["bundle_id"], r["window"], r["text"],
                       r["source"]) if r else None

    def activity(self, since: float, until: float | None = None) -> list[dict[str, Any]]:
        """Per app: captures, first and last seen, and its most common windows."""
        where, params = self._range(since, until, "")
        with self._lock:
            rows = self._conn.execute(
                f"SELECT app, window, COUNT(*) AS n, MIN(ts) AS first, MAX(ts) AS last "
                f"FROM captures c WHERE 1=1 {where} GROUP BY app, window ORDER BY n DESC",
                params).fetchall()
        apps: dict[str, dict[str, Any]] = {}
        for r in rows:
            a = apps.setdefault(r["app"], {"app": r["app"], "captures": 0, "first": r["first"],
                                           "last": r["last"], "windows": []})
            a["captures"] += r["n"]
            a["first"] = min(a["first"], r["first"])
            a["last"] = max(a["last"], r["last"])
            if len(a["windows"]) < 5:
                a["windows"].append(r["window"])
        return sorted(apps.values(), key=lambda a: -a["captures"])

    def delete(self, *, since: float | None = None, until: float | None = None) -> int:
        where, params = self._range(since, until, "")
        with self._lock:
            cur = self._conn.execute(f"DELETE FROM captures WHERE 1=1 {where.replace('c.', '')}",
                                     params)
            self._conn.commit()
            return cur.rowcount

    def prune(self, retention_days: float, now: float | None = None) -> int:
        cutoff = (time.time() if now is None else now) - retention_days * 86400
        return self.delete(until=cutoff)

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0])

    def last(self) -> Capture | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM captures ORDER BY ts DESC LIMIT 1").fetchone()
        return Capture(r["id"], r["ts"], r["app"], r["bundle_id"], r["window"], r["text"],
                       r["source"]) if r else None

    @staticmethod
    def _range(since: float | None, until: float | None, app: str) -> tuple[str, list[Any]]:
        where, params = "", []
        if since is not None:
            where += " AND c.ts >= ?"
            params.append(since)
        if until is not None:
            where += " AND c.ts < ?"
            params.append(until)
        if app:
            where += " AND lower(c.app) = lower(?)"
            params.append(app)
        return where, params

    def close(self) -> None:
        self._conn.close()
