"""Unit tests: vision auto OCR-only on text-heavy screens (Phase 1)."""
from __future__ import annotations

import pytest

from aether.perception import vision
from aether.perception.ocr import TextRegion


def _heavy() -> list[TextRegion]:
    return [TextRegion("a paragraph of words " * 2, 0.95, 0.1, 0.1, 0.4, 0.03) for _ in range(20)]


def _sparse() -> list[TextRegion]:
    return [TextRegion("Go", 0.9, 0.1, 0.1, 0.05, 0.03)]


@pytest.mark.unit
class TestAnalyzeScreenAutoOCR:
    def test_text_heavy_skips_cloud(self, monkeypatch) -> None:  # noqa: ANN001
        import aether.perception.ocr as ocr_mod

        monkeypatch.setattr(ocr_mod, "recognize", lambda p: ("fmt", _heavy(), (1000, 800)))
        called = {"n": 0}

        def _cloud(_path: str) -> str:
            called["n"] += 1
            return "vlm"

        out = vision.analyze_screen("x.png", use_cloud=True, cloud_analyze_fn=_cloud)
        assert out["content_class"] == "text_heavy"
        assert called["n"] == 0  # cloud VLM skipped

    def test_sparse_uses_cloud(self, monkeypatch) -> None:  # noqa: ANN001
        import aether.perception.ocr as ocr_mod

        monkeypatch.setattr(ocr_mod, "recognize", lambda p: ("fmt", _sparse(), (1000, 800)))
        called = {"n": 0}

        def _cloud(_path: str) -> str:
            called["n"] += 1
            return "vlm"

        out = vision.analyze_screen("x.png", use_cloud=True, cloud_analyze_fn=_cloud)
        assert out["content_class"] != "text_heavy"
        assert called["n"] == 1
