"""Embedding upgrade entry point (Phase 11, FR-24).

Re-exports from aether.memory.embeddings for backward compatibility.
"""
from __future__ import annotations

from .embeddings import (
    FallbackEmbedder,
    HashEmbedder,
    LocalEmbedder,
    OpenAIEmbedder,
    cosine_similarity,
    create_embedder,
    hash_embed,
    upgrade_available,
)

__all__ = [
    "FallbackEmbedder",
    "HashEmbedder",
    "LocalEmbedder",
    "OpenAIEmbedder",
    "cosine_similarity",
    "create_embedder",
    "hash_embed",
    "upgrade_available",
]
