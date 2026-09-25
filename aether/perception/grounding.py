"""Reading a vision model's coordinates back onto the screen.

Models disagree on coordinate conventions: some answer in the pixels of the
image they were sent, others on a 0–1000 grid (UI-TARS, several GLM/Qwen
models). Guessing wrong puts every pointer and click in the wrong place, so
the convention is measured per model by scripts/calibrate_grounding.py and
stored as ``coord_space`` (see screen.grounding_settings()).

Pure functions — unit-testable without a Mac.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from statistics import median
from typing import Iterable

from .screen import Capture

COORD_SPACES = ("pixels", "norm1000")

_JSON_OBJ_RE = re.compile(r"\{[^{}]*\}")
_POINT_TAG_RE = re.compile(r"<point>\s*(-?[\d.]+)[\s,]+(-?[\d.]+)\s*</point>", re.I)
_BOX_TOKEN_RE = re.compile(r"<\|box_start\|>\s*\(?\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)?", re.I)
_PAIR_RE = re.compile(r"[\[(]\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*(?:,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*)?[\])]")


def parse_point(text: str) -> tuple[float, float] | None:
    """Extract one (x, y) from a model reply.

    Accepts JSON ``{"x": .., "y": ..}`` (null → None), ``<point>x y</point>``,
    ``<|box_start|>(x,y)``, ``[x, y]`` / ``(x, y)``, and boxes
    ``[x1, y1, x2, y2]`` (their center).
    """
    if not text:
        return None
    for m in _JSON_OBJ_RE.finditer(text):
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "x" in obj and "y" in obj:
            if obj["x"] is None or obj["y"] is None:
                return None
            try:
                return float(obj["x"]), float(obj["y"])
            except (TypeError, ValueError):
                return None
    for rx in (_POINT_TAG_RE, _BOX_TOKEN_RE):
        m = rx.search(text)
        if m:
            return float(m.group(1)), float(m.group(2))
    m = _PAIR_RE.search(text)
    if m:
        if m.group(3) is not None:
            x1, y1, x2, y2 = (float(m.group(i)) for i in range(1, 5))
            return (x1 + x2) / 2.0, (y1 + y2) / 2.0
        return float(m.group(1)), float(m.group(2))
    return None


def to_screen_point(cap: Capture, x: float, y: float,
                    coord_space: str = "pixels") -> tuple[float, float]:
    """Model coordinates on ``cap`` → global screen points (click-ready)."""
    if coord_space == "norm1000":
        return cap.normalized_to_points(x / 1000.0, y / 1000.0)
    return cap.to_points(x, y)


def resolve_coord_space(setting: str | None) -> str:
    """'auto' (uncalibrated) reads as pixels, the convention we ask for."""
    return setting if setting in COORD_SPACES else "pixels"


@dataclass(frozen=True)
class Target:
    """A known UI element (from the accessibility tree), in global points."""

    label: str
    x: float
    y: float
    w: float
    h: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    def contains(self, gx: float, gy: float, tolerance: float = 4.0) -> bool:
        return (self.x - tolerance <= gx <= self.x + self.w + tolerance
                and self.y - tolerance <= gy <= self.y + self.h + tolerance)


@dataclass(frozen=True)
class Score:
    coord_space: str
    max_image_edge: int
    samples: int
    answered: int
    hits: int
    median_error_pt: float

    @property
    def hit_rate(self) -> float:
        return self.hits / self.samples if self.samples else 0.0

    def as_dict(self) -> dict:
        return {
            "coord_space": self.coord_space,
            "max_image_edge": self.max_image_edge,
            "samples": self.samples,
            "answered": self.answered,
            "hits": self.hits,
            "hit_rate": round(self.hit_rate, 3),
            "median_error_pt": round(self.median_error_pt, 1),
        }


def score(cap: Capture, answers: Iterable[tuple[Target, tuple[float, float] | None]],
          coord_space: str, max_image_edge: int) -> Score:
    """Hit rate and median distance (points) for one convention."""
    samples = answered = hits = 0
    errors: list[float] = []
    for target, raw in answers:
        samples += 1
        if raw is None:
            continue
        answered += 1
        gx, gy = to_screen_point(cap, raw[0], raw[1], coord_space)
        cx, cy = target.center
        errors.append(math.hypot(gx - cx, gy - cy))
        if target.contains(gx, gy):
            hits += 1
    return Score(coord_space, max_image_edge, samples, answered, hits,
                 median(errors) if errors else float("inf"))


def choose(scores: Iterable[Score]) -> Score | None:
    """Best convention + size: most hits, then lowest error, then smaller image."""
    ranked = sorted(scores, key=lambda s: (-s.hit_rate, s.median_error_pt, s.max_image_edge))
    return ranked[0] if ranked else None
