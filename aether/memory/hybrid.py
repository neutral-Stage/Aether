"""Hybrid search for local memory: full text + vectors, fused by rank.

Words and meaning fail differently: full-text search (SQLite FTS5, BM25)
nails names, numbers and exact phrases; embeddings catch paraphrases. Each
query runs both, and the lists are fused with reciprocal rank fusion
(score = Σ 1/(60 + rank)), then nudged by recency (the Natively recipe).

Every stored vector is stamped with the model that made it (``embed_model``).
Rows from another model are never compared with the current one; reindex()
re-embeds them in small batches, so switching embedders needs no migration.
At personal scale (thousands of rows) exact cosine in numpy is fast enough.
"""
from __future__ import annotations

import math
import re
import sqlite3
import time
from collections.abc import Sequence
from typing import Any

import numpy as np

RRF_K = 60
RECENCY_WEIGHT = 0.2          # up to +20% for brand-new rows
RECENCY_HALF_LIFE_DAYS = 30.0
CANDIDATES = 50
_STOP = frozenset({"a", "an", "the", "to", "of", "and", "or", "in", "on", "for", "is", "it",
                   "my", "me", "i", "you", "what", "how", "do", "does", "did", "with", "at"})


def fts_query(text: str, max_terms: int = 12) -> str:
    """User text → a safe FTS5 query: quoted terms joined by OR."""
    terms = [t for t in re.findall(r"\w+", (text or "").lower()) if t not in _STOP and len(t) > 1]
    seen: list[str] = []
    for t in terms:
        if t not in seen:
            seen.append(t)
    return " OR ".join(f'"{t}"' for t in seen[:max_terms])


def recency_factor(created_at: float, now: float | None = None) -> float:
    age_days = max(0.0, ((now or time.time()) - float(created_at or 0)) / 86400.0)
    return 1.0 + RECENCY_WEIGHT * math.exp(-age_days * math.log(2) / RECENCY_HALF_LIFE_DAYS)


def rrf(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, row_id in enumerate(ranking, 1):
            scores[row_id] = scores.get(row_id, 0.0) + 1.0 / (k + rank)
    return scores


class HybridIndex:
    """Hybrid search over one table.

    The table needs ``id INTEGER PRIMARY KEY``, the text columns, ``embedding
    BLOB``, and ``created_at REAL``; ensure_schema() adds ``embed_model`` and
    the FTS5 mirror (kept in sync by triggers).
    """

    def __init__(self, conn: sqlite3.Connection, table: str, text_cols: Sequence[str],
                 embedder: Any) -> None:
        self.conn = conn
        self.table = table
        self.text_cols = list(text_cols)
        self.embedder = embedder
        self.fts = f"{table}_fts"
        self.fts_ok = False

    @property
    def model_id(self) -> str:
        return str(getattr(self.embedder, "model_id", getattr(self.embedder, "provider", "?")))

    def ensure_schema(self) -> None:
        cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({self.table})")}
        if "embed_model" not in cols:
            self.conn.execute(f"ALTER TABLE {self.table} ADD COLUMN embed_model TEXT")
        try:
            exists = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (self.fts,)).fetchone()
            cols_sql = ", ".join(self.text_cols)
            self.conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {self.fts} USING fts5("
                f"{cols_sql}, content='{self.table}', content_rowid='id', "
                "tokenize='porter unicode61 remove_diacritics 2')")
            new = ", ".join(f"new.{c}" for c in self.text_cols)
            old = ", ".join(f"old.{c}" for c in self.text_cols)
            self.conn.executescript(f"""
                CREATE TRIGGER IF NOT EXISTS {self.table}_ai AFTER INSERT ON {self.table} BEGIN
                  INSERT INTO {self.fts}(rowid, {cols_sql}) VALUES (new.id, {new});
                END;
                CREATE TRIGGER IF NOT EXISTS {self.table}_ad AFTER DELETE ON {self.table} BEGIN
                  INSERT INTO {self.fts}({self.fts}, rowid, {cols_sql}) VALUES('delete', old.id, {old});
                END;
                CREATE TRIGGER IF NOT EXISTS {self.table}_au AFTER UPDATE ON {self.table} BEGIN
                  INSERT INTO {self.fts}({self.fts}, rowid, {cols_sql}) VALUES('delete', old.id, {old});
                  INSERT INTO {self.fts}(rowid, {cols_sql}) VALUES (new.id, {new});
                END;
            """)
            if not exists:   # index rows written before FTS existed
                self.conn.execute(f"INSERT INTO {self.fts}({self.fts}) VALUES('rebuild')")
            self.fts_ok = True
        except sqlite3.OperationalError:   # SQLite built without FTS5
            self.fts_ok = False
        self.conn.commit()

    # -- vectors --------------------------------------------------------------------------------

    def embed_passage(self, text: str) -> np.ndarray:
        fn = getattr(self.embedder, "embed_passage", None) or self.embedder.embed
        return np.asarray(fn(text), dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        fn = getattr(self.embedder, "embed_query", None) or self.embedder.embed
        return np.asarray(fn(text), dtype=np.float32)

    def row_text(self, row: sqlite3.Row) -> str:
        return "\n".join(str(row[c] or "") for c in self.text_cols)

    def stale_count(self) -> int:
        return int(self.conn.execute(
            f"SELECT COUNT(*) FROM {self.table} WHERE embed_model IS NOT ?",
            (self.model_id,)).fetchone()[0])

    def reindex(self, limit: int = 200) -> int:
        """Re-embed up to ``limit`` rows made by another model."""
        cols = ", ".join(self.text_cols)
        rows = self.conn.execute(
            f"SELECT id, {cols} FROM {self.table} WHERE embed_model IS NOT ? LIMIT ?",
            (self.model_id, limit)).fetchall()
        for row in rows:
            emb = self.embed_passage(self.row_text(row))
            self.conn.execute(
                f"UPDATE {self.table} SET embedding=?, embed_model=? WHERE id=?",
                (emb.tobytes(), self.model_id, row["id"]))
        if rows:
            self.conn.commit()
        return len(rows)

    # -- search ---------------------------------------------------------------------------------

    def _vector_ranking(self, query: str, where: str, params: Sequence[Any],
                        min_sim: float) -> tuple[list[int], dict[int, float]]:
        q = self.embed_query(query)
        rows = self.conn.execute(
            f"SELECT id, embedding FROM {self.table} WHERE embed_model = ? "
            + (f"AND ({where})" if where else ""), (self.model_id, *params)).fetchall()
        sims: dict[int, float] = {}
        for row in rows:
            emb = np.frombuffer(row["embedding"], dtype=np.float32)
            if emb.shape == q.shape:
                sims[int(row["id"])] = float(np.dot(q, emb))
        ranked = [i for i, s in sorted(sims.items(), key=lambda kv: -kv[1]) if s >= min_sim]
        return ranked[:CANDIDATES], sims

    def _fts_ranking(self, query: str, where: str, params: Sequence[Any]) -> list[int]:
        match = fts_query(query)
        if not self.fts_ok or not match:
            return []
        sql = (f"SELECT {self.fts}.rowid AS id FROM {self.fts} "
               f"JOIN {self.table} ON {self.table}.id = {self.fts}.rowid "
               f"WHERE {self.fts} MATCH ? " + (f"AND ({where}) " if where else "")
               + f"ORDER BY bm25({self.fts}) LIMIT {CANDIDATES}")
        try:
            return [int(r["id"]) for r in self.conn.execute(sql, (match, *params)).fetchall()]
        except sqlite3.OperationalError:
            return []

    def search(self, query: str, *, limit: int = 5, where: str = "",
               params: Sequence[Any] = (), min_sim: float = 0.3) -> list[tuple[int, float, float]]:
        """[(row id, fused score, cosine)] best first. A row qualifies by
        matching words or by cosine ≥ min_sim."""
        if self.stale_count():
            self.reindex(limit=100)
        vec_rank, sims = self._vector_ranking(query, where, params, min_sim)
        fts_rank = self._fts_ranking(query, where, params)
        fused = rrf([vec_rank, fts_rank])
        if not fused:
            return []
        ids = list(fused)
        marks = ",".join("?" * len(ids))
        created = {int(r["id"]): float(r["created_at"] or 0) for r in self.conn.execute(
            f"SELECT id, created_at FROM {self.table} WHERE id IN ({marks})", ids)}
        now = time.time()
        scored = [(i, fused[i] * recency_factor(created.get(i, now), now), sims.get(i, 0.0))
                  for i in ids]
        scored.sort(key=lambda t: -t[1])
        return scored[:limit]
