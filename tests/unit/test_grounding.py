"""Parsing model coordinates and scoring coordinate conventions."""
from __future__ import annotations

import pytest

from aether.perception import grounding
from aether.perception.screen import Capture, DisplayInfo

CAP = Capture("/g.png", DisplayInfo(1, 1, 0.0, 0.0, 1000.0, 500.0, 2.0, True), 1600, 800)


@pytest.mark.parametrize(("text", "expected"), [
    ('{"x": 120, "y": 45}', (120, 45)),
    ('Sure: {"x": 12.5, "y": 7}', (12.5, 7)),
    ('{"x": null, "y": null}', None),
    ("<point>300 200</point>", (300, 200)),
    ("<|box_start|>(410,220)<|box_end|>", (410, 220)),
    ("click at [100, 50]", (100, 50)),
    ("box [10, 20, 30, 40]", (20, 30)),
    ("no idea", None),
    ("", None),
])
def test_parse_point(text: str, expected) -> None:  # noqa: ANN001
    got = grounding.parse_point(text)
    assert got == (pytest.approx(expected) if expected else None)


def test_to_screen_point_conventions() -> None:
    # 1600 px image of a 1000-pt display: 1.6 px per point.
    assert grounding.to_screen_point(CAP, 800, 400, "pixels") == pytest.approx((500, 250))
    assert grounding.to_screen_point(CAP, 500, 500, "norm1000") == pytest.approx((500, 250))
    assert grounding.resolve_coord_space("auto") == "pixels"
    assert grounding.resolve_coord_space("norm1000") == "norm1000"


def test_score_and_choose() -> None:
    t = grounding.Target("Send", 480, 240, 40, 20)       # center (500, 250)
    answers = [(t, (800, 400)), (t, (801, 401)), (t, None)]
    pixels = grounding.score(CAP, answers, "pixels", 1600)
    norm = grounding.score(CAP, answers, "norm1000", 1600)
    assert (pixels.hits, pixels.answered, pixels.samples) == (2, 2, 3)
    assert norm.hits == 0
    assert grounding.choose([norm, pixels]) is pixels
    smaller = grounding.Score("pixels", 1280, 3, 2, 2, pixels.median_error_pt)
    assert grounding.choose([pixels, smaller]) is smaller  # ties prefer the cheaper image
