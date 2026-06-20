"""Long-term memory — local vector store (§6.6).

SQLite-backed store with configurable embeddings (hash / OpenAI / local)
and cosine similarity retrieval.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .embeddings import HashEmbedder, cosine_similarity, create_embedder

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "memory.db"

# Backward compat — tests and skills import these
_DIM = HashEmbedder.dimension
_embed = HashEmbedder().embed
_cosine = cosine_similarity


@dataclass
class MemoryEntry:
    id: int
    kind: str
    text: str
    metadata: dict[str, Any]
    score: float = 0.0
    created_at: float = 0.0


class MemoryStore:
    """Session-external memory: preferences, traces, app quirks, corrections."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        embedding_provider: str = "hash",
        openai_api_key: str | None = None,
        openai_model: str = "text-embedding-3-small",
        local_model: str = "all-MiniLM-L6-v2",
    ):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._embedder = create_embedder(
            embedding_provider,
            openai_api_key=openai_api_key,
            openai_model=openai_model,
            local_model=local_model,
        )
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                embedding BLOB NOT NULL,
                metadata TEXT,
                created_at REAL NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind)"
        )
        self._conn.commit()

    def _embed_text(self, text: str) -> np.ndarray:
        return self._embedder.embed(text)

    def _score_embedding(self, query: str, query_emb: np.ndarray, stored: bytes) -> float:
        emb = np.frombuffer(stored, dtype=np.float32)
        if emb.shape[0] == query_emb.shape[0]:
            return cosine_similarity(query_emb, emb)
        # Legacy hash rows when query uses a different provider dimension
        if emb.shape[0] == _DIM:
            hash_q = _embed(query)
            return cosine_similarity(hash_q, emb)
        return 0.0

    def remember(
        self,
        text: str,
        kind: str = "fact",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        emb = self._embed_text(text)
        meta = dict(metadata or {})
        meta.setdefault("embedding_provider", self._embedder.provider)
        meta_json = json.dumps(meta)
        cur = self._conn.execute(
            "INSERT INTO memories (kind, text, embedding, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (kind, text, emb.tobytes(), meta_json, time.time()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def retrieve(self, query: str, limit: int = 5, kind: str | None = None) -> list[MemoryEntry]:
        q_emb = self._embed_text(query)
        sql = "SELECT id, kind, text, embedding, metadata, created_at FROM memories"
        params: list[Any] = []
        if kind:
            sql += " WHERE kind = ?"
            params.append(kind)
        rows = self._conn.execute(sql, params).fetchall()
        scored: list[MemoryEntry] = []
        for row in rows:
            score = self._score_embedding(query, q_emb, row["embedding"])
            meta = json.loads(row["metadata"] or "{}")
            scored.append(MemoryEntry(
                id=row["id"],
                kind=row["kind"],
                text=row["text"],
                metadata=meta,
                score=score,
                created_at=row["created_at"],
            ))
        scored.sort(key=lambda e: e.score, reverse=True)
        return scored[:limit]

    def store_task_trace(self, goal: str, steps: list[str], success: bool) -> int:
        text = f"Task: {goal}\nSteps:\n" + "\n".join(f"- {s}" for s in steps)
        return self.remember(
            text,
            kind="trace",
            metadata={"goal": goal, "success": success, "step_count": len(steps)},
        )

    def prompt_slice(self, query: str, limit: int = 4) -> str:
        """Retrieve relevant memories formatted for system prompt injection."""
        entries = self.retrieve(query, limit=limit)
        if not entries:
            return ""
        lines = ["Relevant long-term memory:"]
        for e in entries:
            if e.score < 0.05:
                continue
            lines.append(f"- [{e.kind}] {e.text[:300]}")
        return "\n".join(lines) if len(lines) > 1 else ""

    def close(self) -> None:
        self._conn.close()
