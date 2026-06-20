"""OCR coordinate scaling tests (Phase 8)."""
from __future__ import annotations

from aether.perception.ocr import TextRegion, scale_region_to_pixels, scale_regions_to_pixels


class TestOCRScaling:
    def test_scale_region_to_pixels(self) -> None:
        region = TextRegion("OK", 0.9, 0.25, 0.5, 0.1, 0.05)
        scaled = scale_region_to_pixels(region, 2000, 1000)
        assert scaled.x == 500.0
        assert scaled.y == 500.0
        assert scaled.w == 200.0
        assert scaled.h == 50.0

    def test_scale_regions_batch(self) -> None:
        regions = [TextRegion("A", 1.0, 0.0, 0.0, 1.0, 1.0)]
        out = scale_regions_to_pixels(regions, 100, 50)
        assert out[0].w == 100.0
        assert out[0].h == 50.0

    def test_zero_dimensions_passthrough(self) -> None:
        region = TextRegion("A", 1.0, 0.5, 0.5, 0.1, 0.1)
        out = scale_regions_to_pixels([region], 0, 0)
        assert out[0].x == 0.5
