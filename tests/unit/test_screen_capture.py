"""Unit tests: screen capture degrades gracefully (Phase 1)."""
from __future__ import annotations

import subprocess

import pytest

from aether.core.world_model import WorldModel
from aether.perception import screen


@pytest.mark.unit
class TestCaptureHardening:
    def test_try_capture_returns_none_on_failure(self, monkeypatch) -> None:  # noqa: ANN001
        def _boom(*a, **k):  # noqa: ANN002, ANN003
            raise subprocess.CalledProcessError(1, "screencapture")

        monkeypatch.setattr(screen, "capture_to_file", _boom)
        screen._warned_capture = False  # noqa: SLF001
        assert screen.try_capture_to_file() is None

    def test_world_capture_returns_none(self, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(screen, "try_capture_to_file", lambda path=None: None)
        w = WorldModel(ax_cache_ttl_ms=0)
        assert w.capture_screenshot() is None
