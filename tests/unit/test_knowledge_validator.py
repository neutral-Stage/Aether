"""Unit tests for knowledge pack validator (Phase 11)."""
from __future__ import annotations

from pathlib import Path

import pytest

from aether.knowledge.loader import PACKS_DIR, list_packs
from aether.knowledge.validator import validate_pack_data, validate_pack_directory, validate_pack_file


@pytest.mark.unit
class TestKnowledgeValidator:
    def test_valid_minimal_pack(self) -> None:
        errors = validate_pack_data({"app": "TestApp", "tier": 1})
        assert errors == []

    def test_missing_app_and_tier(self) -> None:
        errors = validate_pack_data({})
        assert any("app" in e for e in errors)
        assert any("tier" in e for e in errors)

    def test_invalid_tier(self) -> None:
        errors = validate_pack_data({"app": "X", "tier": 9})
        assert any("tier" in e for e in errors)

    def test_builtin_packs_validate(self) -> None:
        errors = validate_pack_directory(PACKS_DIR)
        assert errors == {}, f"Pack errors: {errors}"

    def test_list_packs_includes_finder(self) -> None:
        packs = list_packs()
        assert "finder" in packs

    def test_invalid_yaml_file(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("app: [unclosed", encoding="utf-8")
        errors = validate_pack_file(bad)
        assert len(errors) >= 1
