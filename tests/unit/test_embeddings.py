"""Unit tests for embedding providers (Phase 11)."""
from __future__ import annotations

import pytest

from aether.memory.embeddings import (
    HashEmbedder,
    cosine_similarity,
    create_embedder,
    hash_embed,
    upgrade_available,
)
from aether.memory.embeddings_upgrade import upgrade_available as upgrade_reexport


@pytest.mark.unit
class TestEmbeddings:
    def test_hash_embed_normalized(self) -> None:
        vec = hash_embed("open finder and go to downloads")
        assert vec.shape[0] == 256
        norm = float((vec * vec).sum() ** 0.5)
        assert abs(norm - 1.0) < 0.01

    def test_hash_similar_text_scores_higher(self) -> None:
        a = hash_embed("open Safari browser")
        b = hash_embed("open Safari and navigate")
        c = hash_embed("compose email in Mail")
        assert cosine_similarity(a, b) > cosine_similarity(a, c)

    def test_create_embedder_hash_default(self) -> None:
        emb = create_embedder("hash")
        vec = emb.embed("test memory fact")
        assert vec.shape[0] == HashEmbedder.dimension
        assert emb.provider == "hash"

    def test_create_embedder_openai_without_key_falls_back(self) -> None:
        emb = create_embedder("openai", openai_api_key=None)
        vec = emb.embed("fallback test")
        assert vec.shape[0] == HashEmbedder.dimension

    def test_upgrade_available_is_bool(self) -> None:
        assert isinstance(upgrade_available(), bool)
        assert upgrade_available() == upgrade_reexport()
