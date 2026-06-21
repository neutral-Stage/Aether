"""Unit tests: pack pre-warm closes the cold-start selection gap (Phase 1)."""
from __future__ import annotations

import pytest

from aether.knowledge import loader


@pytest.mark.unit
def test_prewarm_registers_yaml_bundle_ids(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    pack = tmp_path / "novelapp.yaml"
    pack.write_text(
        "app: NovelApp\ntier: 2\nbundle_ids:\n  - com.test.NovelApp\naliases:\n  - novelapp\n"
    )
    monkeypatch.setenv("AETHER_PACKS_DIR", str(tmp_path))
    loader._load_pack_file.cache_clear()  # noqa: SLF001
    loader._prewarmed = False  # noqa: SLF001
    loader._BUNDLE_TO_PACK.pop("com.test.NovelApp", None)  # ensure not pre-registered

    try:
        key = loader.resolve_pack_key(bundle_id="com.test.NovelApp")
        assert key == "novelapp"
    finally:
        loader._BUNDLE_TO_PACK.pop("com.test.NovelApp", None)  # noqa: SLF001
