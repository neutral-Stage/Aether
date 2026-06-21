"""Unit tests for the screen-content classifier (Phase 1)."""
from __future__ import annotations

import pytest

from aether.perception.ocr import TextRegion, classify_screen_content


def _region(text: str, conf: float, w: float, h: float) -> TextRegion:
    return TextRegion(text=text, confidence=conf, x=0.1, y=0.1, w=w, h=h)


@pytest.mark.unit
class TestClassifier:
    def test_text_heavy(self) -> None:
        regions = [_region("a paragraph of words " * 2, 0.95, 0.4, 0.03) for _ in range(20)]
        out = classify_screen_content(regions, 1000, 800)
        assert out["label"] == "text_heavy"
        assert out["char_count"] >= 200

    def test_empty_when_no_regions(self) -> None:
        out = classify_screen_content([], 1000, 800)
        assert out["label"] == "empty"

    def test_graphical_few_tiny_boxes(self) -> None:
        regions = [_region("x", 0.6, 0.001, 0.001) for _ in range(2)]
        out = classify_screen_content(regions, 1000, 800)
        assert out["label"] == "graphical"

    def test_unknown_when_dims_invalid(self) -> None:
        regions = [_region("hello world", 0.9, 0.2, 0.05)]
        out = classify_screen_content(regions, 0, 0)
        assert out["label"] == "unknown"

    def test_low_confidence_filtered(self) -> None:
        regions = [_region("noise " * 50, 0.1, 0.5, 0.5) for _ in range(10)]
        out = classify_screen_content(regions, 1000, 800)
        # all below min_conf → filtered → empty
        assert out["region_count"] == 0
        assert out["label"] == "empty"


@pytest.mark.unit
def test_recognize_returns_triple(monkeypatch) -> None:  # noqa: ANN001
    import aether.perception.ocr as ocr_mod

    fake = [TextRegion("hi", 0.9, 0.1, 0.1, 0.2, 0.05)]
    monkeypatch.setattr(ocr_mod, "recognize_text", lambda p: fake)
    monkeypatch.setattr(ocr_mod, "image_dimensions", lambda p: (1000, 800))
    formatted, regions, dims = ocr_mod.recognize("x.png")
    assert "OCR found 1 regions" in formatted
    assert regions == fake
    assert dims == (1000, 800)
