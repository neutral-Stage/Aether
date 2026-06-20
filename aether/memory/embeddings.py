"""Embedding providers for long-term memory (Phase 11, FR-24).

Fallback chain: configured provider → hash (always available).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Protocol

import numpy as np

log = logging.getLogger(__name__)

_HASH_DIM = 256
_OPENAI_DIM = 1536
_LOCAL_DIM = 384  # all-MiniLM-L6-v2


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def hash_embed(text: str, *, dim: int = _HASH_DIM) -> np.ndarray:
    """Deterministic bag-of-words hash embedding (no external model)."""
    vec = np.zeros(dim, dtype=np.float32)
    for tok in _tokenize(text):
        h = hash(tok) % dim
        vec[h] += 1.0
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return 0.0
    return float(np.dot(a, b))


class Embedder(Protocol):
    provider: str
    dimension: int

    def embed(self, text: str) -> np.ndarray: ...


class HashEmbedder:
    provider = "hash"
    dimension = _HASH_DIM

    def embed(self, text: str) -> np.ndarray:
        return hash_embed(text, dim=self.dimension)


class OpenAIEmbedder:
    provider = "openai"
    dimension = _OPENAI_DIM

    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        self._api_key = api_key
        self._model = model

    def embed(self, text: str) -> np.ndarray:
        from openai import OpenAI

        client = OpenAI(api_key=self._api_key)
        resp = client.embeddings.create(input=text or " ", model=self._model)
        vec = np.array(resp.data[0].embedding, dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec


class LocalEmbedder:
    """sentence-transformers local model (optional dependency)."""

    provider = "local"
    dimension = _LOCAL_DIM

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
            self.dimension = int(self._model.get_sentence_embedding_dimension())
        return self._model

    def embed(self, text: str) -> np.ndarray:
        model = self._load()
        vec = model.encode(text or " ", normalize_embeddings=True)
        return np.asarray(vec, dtype=np.float32)


class FallbackEmbedder:
    """Try primary provider; fall back to hash on failure."""

    def __init__(self, primary: Embedder, fallback: Embedder | None = None):
        self._primary = primary
        self._fallback = fallback or HashEmbedder()
        self.provider = primary.provider
        self.dimension = primary.dimension

    def embed(self, text: str) -> np.ndarray:
        try:
            return self._primary.embed(text)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Embedding provider %s failed (%s); using hash fallback",
                self._primary.provider,
                exc,
            )
            self.provider = self._fallback.provider
            self.dimension = self._fallback.dimension
            return self._fallback.embed(text)


def create_embedder(
    provider: str = "hash",
    *,
    openai_api_key: str | None = None,
    openai_model: str = "text-embedding-3-small",
    local_model: str = "all-MiniLM-L6-v2",
) -> Embedder:
    """Create embedder with hash fallback when openai/local unavailable."""
    provider = (provider or "hash").lower().strip()
    fallback = HashEmbedder()

    if provider == "openai":
        key = openai_api_key or os.getenv("OPENAI_API_KEY")
        if not key:
            log.warning("memory.embedding_provider=openai but no OPENAI_API_KEY; using hash")
            return fallback
        return FallbackEmbedder(OpenAIEmbedder(key, model=openai_model), fallback)

    if provider == "local":
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            log.warning(
                "memory.embedding_provider=local but sentence-transformers not installed; "
                "using hash"
            )
            return fallback
        return FallbackEmbedder(LocalEmbedder(model_name=local_model), fallback)

    return fallback


def upgrade_available() -> bool:
    """True if a non-hash embedding backend can be used."""
    if os.getenv("OPENAI_API_KEY"):
        return True
    try:
        import sentence_transformers  # noqa: F401

        return True
    except ImportError:
        return False
