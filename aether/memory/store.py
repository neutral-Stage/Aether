"""Long-term memory — local, searchable by words and meaning (§6.6, Phase D2).

SQLite store searched with HybridIndex: full-text (FTS5) and embeddings
fused by rank, with a recency nudge. Each vector records the model that
made it, so switching embedders re-embeds old rows instead of mixing them.

Writes are scanned: text that tries to instruct an AI is refused or kept
out of prompts, because memories are re-injected into future runs (audit
residual 7).
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
from .hybrid import HybridIndex

from ..core.paths import ROOT, data_dir, resolve_data_path  # noqa: F401

DEFAULT_DB = data_dir() / "memory.db"  # informational; resolved per instance

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
        self.db_path = resolve_data_path(db_path, "memory.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._embedder = create_embedder(
            embedding_provider,
            openai_api_key=openai_api_key,
            openai_model=openai_model,
            local_model=local_model,
        )
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self.index = HybridIndex(self._conn, "memories", ["text"], self._embedder)
        self.index.ensure_schema()

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
        return self.index.embed_passage(text)

    @property
    def min_similarity(self) -> float:
        """Cosine a row needs to count as related without shared words.
        e5 vectors sit close together, so its bar is much higher."""
        provider = getattr(self._embedder, "provider", "hash")
        return {"e5": 0.82}.get(provider, 0.35)

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
        """Store a memory. Returns its id, or 0 when the text was refused
        (it tries to instruct an AI outright)."""
        from ..core.security import InjectionSeverity, scan_injection

        meta = dict(metadata or {})
        scan = scan_injection(text)
        if scan.severity == InjectionSeverity.HIGH:
            return 0
        if scan.severity == InjectionSeverity.MEDIUM:
            meta["suspicious"] = True       # kept, but never put into a prompt
        emb = self._embed_text(text)
        meta.setdefault("embedding_provider", self._embedder.provider)
        cur = self._conn.execute(
            "INSERT INTO memories (kind, text, embedding, metadata, created_at, embed_model) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (kind, text, emb.tobytes(), json.dumps(meta), time.time(), self.index.model_id),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def retrieve(self, query: str, limit: int = 5, kind: str | None = None) -> list[MemoryEntry]:
        """Best matches by words and meaning (see HybridIndex), newest nudged up."""
        hits = self.index.search(query, limit=limit, where="kind = ?" if kind else "",
                                 params=(kind,) if kind else (), min_sim=self.min_similarity)
        if not hits:
            return []
        by_id = {int(r["id"]): r for r in self._conn.execute(
            "SELECT id, kind, text, metadata, created_at FROM memories WHERE id IN ("
            + ",".join("?" * len(hits)) + ")", [h[0] for h in hits])}
        out: list[MemoryEntry] = []
        for row_id, score, _cos in hits:
            row = by_id.get(row_id)
            if row is None:
                continue
            out.append(MemoryEntry(id=row_id, kind=row["kind"], text=row["text"],
                                   metadata=json.loads(row["metadata"] or "{}"), score=score,
                                   created_at=row["created_at"]))
        return out

    def forget(self, memory_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def store_task_trace(self, goal: str, steps: list[str], success: bool) -> int:
        text = f"Task: {goal}\nSteps:\n" + "\n".join(f"- {s}" for s in steps)
        return self.remember(
            text,
            kind="trace",
            metadata={"goal": goal, "success": success, "step_count": len(steps)},
        )

    def profile(self, limit: int = 8) -> list[MemoryEntry]:
        """What the user said about themselves (onboarding), newest first."""
        rows = self._conn.execute(
            "SELECT id, kind, text, metadata, created_at FROM memories WHERE kind = 'profile' "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            meta = json.loads(r["metadata"] or "{}")
            if not meta.get("suspicious"):
                out.append(MemoryEntry(int(r["id"]), r["kind"], r["text"], meta,
                                       created_at=float(r["created_at"] or 0)))
        return out

    def set_profile(self, key: str, text: str) -> int:
        """Replace the profile answer stored under ``key`` (0 when refused or empty)."""
        for e in self.profile(limit=50):
            if e.metadata.get("question") == key:
                self.forget(e.id)
        text = " ".join((text or "").split())[:400]
        if not text:
            return 0
        return self.remember(text, kind="profile",
                             metadata={"source": "onboarding", "question": key})

    def profile_slice(self) -> str:
        entries = self.profile()
        if not entries:
            return ""
        return "About the user, in their own words:\n" + "\n".join(
            f"- {e.text[:300]}" for e in reversed(entries))

    def prompt_slice(self, query: str, limit: int = 4) -> str:
        """Retrieve relevant memories formatted for system prompt injection."""
        entries = self.retrieve(query, limit=limit + 2)
        if not entries:
            return ""
        lines = ["Relevant long-term memory:"]
        for e in entries:
            if e.metadata.get("suspicious") or e.kind == "profile":   # profile has its own slice
                continue
            lines.append(f"- [{e.kind}] {e.text[:300]}")
            if len(lines) > limit:
                break
        return "\n".join(lines) if len(lines) > 1 else ""

    def close(self) -> None:
        self._conn.close()
