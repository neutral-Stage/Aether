"""Config loading and schema validation tests (Phase 6)."""
from __future__ import annotations

import pytest

from aether.core.config import ROOT, load_config, validate_config


@pytest.mark.unit
class TestConfig:
    def test_load_project_config(self) -> None:
        cfg = load_config(ROOT / "config.yaml")
        assert cfg.max_steps >= 1
        assert cfg.get("beta", "ambient_listening") is False

    def test_validate_rejects_invalid_agent_max_steps(self) -> None:
        raw = {"agent": {"max_steps": 0}}
        with pytest.raises(ValueError, match="max_steps"):
            validate_config(raw, strict=True)

    def test_validate_accepts_minimal_valid_config(self) -> None:
        raw = {
            "version": "0.5.0-beta.1",
            "agent": {"max_steps": 12, "careful": False},
            "voice": {"tts": "none", "stt": "none"},
            "beta": {"ambient_listening": False, "continuous_screen_stream": False},
        }
        assert validate_config(raw, strict=True) == []
