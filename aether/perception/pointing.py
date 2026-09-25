"""Pointing protocol: tags a spoken answer ends with, and where they land.

The talk model answers in plain speech and ends with tags that are stripped
before speaking (the Clicky protocol, extended):

    [POINT:e12:Bluetooth]            element 12 of the numbered AX list (preferred)
    [POINT:640,212:Save:screen1]     pixels in screenshot 1
    [RECT:600,190,120,40:the toolbar] a box, same coordinates
    [SCRIBBLE:10,10;40,12;80,30:this] a stroke through points
    [POINT:none]                     nothing to point at

Element ids come from the accessibility tree, so they point at the real
control. Pixel points are mapped through the screenshot's geometry and the
calibrated coordinate convention, clamped to the display, and snapped to the
smallest accessibility element within ``SNAP_RADIUS_PT``.

``TagStreamParser`` strips tags from streamed text as it arrives, holding
back only a possible partial tag, so speech can start before the reply ends.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import grounding, screen

SNAP_RADIUS_PT = 24.0
_NUM = r"-?\d+(?:\.\d+)?"
_TAIL = r"(?::(?P<label>[^\]]*?))?(?::screen(?P<screen>\d+))?\]"

_ELEM_RE = re.compile(r"\[POINT:e(?P<id>\d+)" + _TAIL, re.I)
_XY_RE = re.compile(rf"\[POINT:(?P<x>{_NUM})\s*,\s*(?P<y>{_NUM})" + _TAIL, re.I)
_RECT_RE = re.compile(rf"\[RECT:(?P<x>{_NUM})\s*,\s*(?P<y>{_NUM})\s*,\s*(?P<w>{_NUM})\s*,"
                      rf"\s*(?P<h>{_NUM})" + _TAIL, re.I)
_SCRIBBLE_RE = re.compile(rf"\[SCRIBBLE:(?P<pts>(?:{_NUM}\s*,\s*{_NUM}\s*;?\s*)+)" + _TAIL, re.I)
_NONE_RE = re.compile(r"\[POINT:none\]", re.I)
_ANY_TAG_RE = re.compile(r"\[(?:POINT|RECT|SCRIBBLE):[^\]]*\]", re.I)
_TAG_START_RE = re.compile(r"\[(?:P(?:O(?:I(?:N(?:T(?::[^\]]*)?)?)?)?)?|"
                           r"R(?:E(?:C(?:T(?::[^\]]*)?)?)?)?|"
                           r"S(?:C(?:R(?:I(?:B(?:B(?:L(?:E(?::[^\]]*)?)?)?)?)?)?)?)?)?$", re.I)


@dataclass
class Tag:
    kind: str                         # point | rect | scribble | none
    label: str = ""
    element_id: int | None = None     # POINT:eN
    x: float | None = None            # image pixels (or grid units) for point/rect
    y: float | None = None
    w: float | None = None
    h: float | None = None
    points: list[tuple[float, float]] = field(default_factory=list)
    screen: int = 1


@dataclass
class Target:
    """A resolved tag in global screen points, ready for the overlay."""

    kind: str                         # point | rect | scribble
    x: float
    y: float
    label: str = ""
    w: float = 0.0
    h: float = 0.0
    points: list[tuple[float, float]] = field(default_factory=list)
    source: str = "image"             # ax | image | snap
    display: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "x": round(self.x, 1), "y": round(self.y, 1),
                "w": round(self.w, 1), "h": round(self.h, 1), "label": self.label,
                "points": [[round(px, 1), round(py, 1)] for px, py in self.points],
                "source": self.source, "display": self.display}


def _label(m: re.Match) -> str:
    return (m.group("label") or "").strip()


def _screen(m: re.Match) -> int:
    return int(m.group("screen") or 1)


def parse_tags(text: str) -> list[Tag]:
    """Every tag in order of appearance."""
    found: list[tuple[int, Tag]] = []
    for m in _NONE_RE.finditer(text):
        found.append((m.start(), Tag("none")))
    for m in _ELEM_RE.finditer(text):
        found.append((m.start(), Tag("point", _label(m), element_id=int(m.group("id")),
                                     screen=_screen(m))))
    for m in _XY_RE.finditer(text):
        found.append((m.start(), Tag("point", _label(m), x=float(m.group("x")),
                                     y=float(m.group("y")), screen=_screen(m))))
    for m in _RECT_RE.finditer(text):
        found.append((m.start(), Tag("rect", _label(m), x=float(m.group("x")),
                                     y=float(m.group("y")), w=float(m.group("w")),
                                     h=float(m.group("h")), screen=_screen(m))))
    for m in _SCRIBBLE_RE.finditer(text):
        pts = [tuple(float(v) for v in pair.split(","))
               for pair in re.split(r"\s*;\s*", m.group("pts").strip().rstrip(";")) if pair]
        found.append((m.start(), Tag("scribble", _label(m), points=pts, screen=_screen(m))))
    return [t for _, t in sorted(found, key=lambda it: it[0])]


def strip_tags(text: str) -> str:
    """The answer without tags, ready to speak."""
    return " ".join(_ANY_TAG_RE.sub(" ", text or "").split())


class TagStreamParser:
    """Strip tags from a streamed reply as it arrives.

    feed() returns the text that is safe to speak now and any tags completed
    in this chunk; a trailing fragment that could still become a tag is held
    back until the next chunk (or close()).
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, chunk: str) -> tuple[str, list[Tag]]:
        self._buf += chunk or ""
        tags: list[Tag] = []
        out: list[str] = []
        while True:
            m = _ANY_TAG_RE.search(self._buf)
            if not m:
                break
            out.append(self._buf[:m.start()])
            tags += parse_tags(m.group(0))
            self._buf = self._buf[m.end():]
        cut = self._buf.rfind("[")
        if cut >= 0 and _TAG_START_RE.match(self._buf[cut:]):
            out.append(self._buf[:cut])
            self._buf = self._buf[cut:]
        else:
            out.append(self._buf)
            self._buf = ""
        return "".join(out), tags

    def close(self) -> tuple[str, list[Tag]]:
        rest, self._buf = self._buf, ""
        text = strip_tags(rest) if _ANY_TAG_RE.search(rest) else rest
        if _TAG_START_RE.match(text.strip()):   # a tag cut off mid-way: never speak it
            text = ""
        return text, parse_tags(rest)


# ---- resolution ----------------------------------------------------------------------------

@dataclass
class PointContext:
    """What the talk model was shown, so its tags can be mapped back."""

    captures: list[screen.Capture]                 # screenshot 1..N as sent
    elements: dict[int, Any] = field(default_factory=dict)   # eN → ax.Element (points)
    snap_elements: list[Any] = field(default_factory=list)   # candidates for snapping
    coord_space: str = "pixels"


def _clamp(cap: screen.Capture, x: float, y: float) -> tuple[float, float]:
    d = cap.display
    if not d.known or d.width <= 0:
        return x, y
    return (min(max(x, d.x), d.x + d.width - 1), min(max(y, d.y), d.y + d.height - 1))


def snap(x: float, y: float, elements: list[Any], cap: screen.Capture | None = None,
         radius: float = SNAP_RADIUS_PT) -> Any | None:
    """Smallest reasonable element containing (or within ``radius`` of) the point."""
    area_cap = 0.25 * cap.display.width * cap.display.height if cap and cap.display.known else 0
    best, best_key = None, None
    for el in elements:
        w, h = float(el.w), float(el.h)
        if w < 4 or h < 4 or (area_cap and w * h > area_cap):
            continue
        dx = max(el.x - x, 0.0, x - (el.x + w))
        dy = max(el.y - y, 0.0, y - (el.y + h))
        dist = (dx * dx + dy * dy) ** 0.5
        if dist > radius:
            continue
        key = (dist > 0, dist, w * h)
        if best_key is None or key < best_key:
            best, best_key = el, key
    return best


def _cap(ctx: PointContext, n: int) -> screen.Capture | None:
    if not ctx.captures:
        return None
    return ctx.captures[min(max(n, 1), len(ctx.captures)) - 1]


def _to_points(ctx: PointContext, cap: screen.Capture, x: float, y: float) -> tuple[float, float]:
    return _clamp(cap, *grounding.to_screen_point(cap, x, y, ctx.coord_space))


def resolve(tag: Tag, ctx: PointContext) -> Target | None:
    """A tag in global screen points (None for [POINT:none] or unknown ids)."""
    if tag.kind == "none":
        return None
    if tag.element_id is not None:
        el = ctx.elements.get(tag.element_id)
        if el is None:
            return None
        cx, cy = el.x + el.w / 2.0, el.y + el.h / 2.0
        label = tag.label or str(getattr(el, "title", "") or "")
        return Target("rect" if tag.kind == "rect" else "point", cx, cy, label,
                      float(el.w), float(el.h), source="ax")
    cap = _cap(ctx, tag.screen)
    if cap is None:
        return None
    display = cap.display.index
    if tag.kind == "scribble":
        pts = [_to_points(ctx, cap, px, py) for px, py in tag.points]
        if not pts:
            return None
        return Target("scribble", pts[0][0], pts[0][1], tag.label, points=pts, display=display)
    if tag.kind == "rect" and tag.w is not None and tag.h is not None:
        x0, y0 = _to_points(ctx, cap, tag.x, tag.y)
        x1, y1 = _to_points(ctx, cap, tag.x + tag.w, tag.y + tag.h)
        return Target("rect", (x0 + x1) / 2, (y0 + y1) / 2, tag.label, x1 - x0, y1 - y0,
                      display=display)
    x, y = _to_points(ctx, cap, tag.x or 0.0, tag.y or 0.0)
    el = snap(x, y, ctx.snap_elements, cap)
    if el is not None:
        return Target("point", el.x + el.w / 2.0, el.y + el.h / 2.0, tag.label,
                      float(el.w), float(el.h), source="snap", display=display)
    return Target("point", x, y, tag.label, display=display)


def resolve_all(tags: list[Tag], ctx: PointContext) -> list[Target]:
    return [t for t in (resolve(tag, ctx) for tag in tags) if t is not None]
