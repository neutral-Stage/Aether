"""Unit tests for ax_text_ratio computation (vision heuristics activation)."""
from __future__ import annotations

import pytest

from aether.core.world_model import compute_ax_text_ratio


@pytest.mark.unit
class TestAxTextRatio:
    def test_empty_ax_returns_one(self) -> None:
        assert compute_ax_text_ratio([], 0) == 1.0

    def test_text_rich(self) -> None:
        els = [{"role": "AXStaticText"} for _ in range(8)] + [{"role": "AXButton"} for _ in range(2)]
        assert compute_ax_text_ratio(els, 10) == pytest.approx(0.8)

    def test_chrome_heavy_low_ratio(self) -> None:
        els = [{"role": "AXButton"} for _ in range(9)] + [{"role": "AXStaticText"}]
        assert compute_ax_text_ratio(els, 10) == pytest.approx(0.1)

    def test_no_text_roles(self) -> None:
        els = [{"role": "AXButton"}, {"role": "AXImage"}, {"role": "AXGroup"}]
        assert compute_ax_text_ratio(els, 3) == 0.0
