"""Unit test: WorldModel carries screen-content fields (Phase 1)."""
from __future__ import annotations

import pytest

from aether.core.world_model import WorldModel


@pytest.mark.unit
def test_content_fields_default_non_triggering() -> None:
    w = WorldModel(ax_cache_ttl_ms=0)
    assert w.screen_content_class == "unknown"
    assert w.text_heavy_score == 0.0
    assert w.ax_text_ratio == 1.0
