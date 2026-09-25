"""On-device OCR via Apple Vision framework (§6.2).

Uses VNRecognizeTextRequest for text in apps with poor AX coverage.
Requires pyobjc-framework-Vision (optional — graceful fallback if missing).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

_VISION_OK = False
try:
    import Vision
    from Foundation import NSURL
    _VISION_OK = True
except Exception:  # pragma: no cover
    pass


@dataclass
class TextRegion:
    text: str
    confidence: float
    x: float
    y: float
    w: float
    h: float

    def describe(self, *, pixel_coords: bool = False, unit: str | None = None) -> str:
        unit = unit or ("px" if pixel_coords else "norm")
        return (f'"{self.text}" conf={self.confidence:.2f} '
                f'@({int(self.x)},{int(self.y)} {int(self.w)}x{int(self.h)} {unit})')


def available() -> bool:
    return _VISION_OK


def _vision_box(box) -> tuple[float, float, float, float]:  # noqa: ANN001
    """Vision's normalized bottom-left box -> normalized top-left (x, y, w, h)."""
    return (float(box.origin.x), float(1.0 - box.origin.y - box.size.height),
            float(box.size.width), float(box.size.height))


def _observations(image_path: str) -> list[tuple[str, float, tuple, Any]]:
    """Run Vision text recognition -> [(line text, confidence, box, candidate)]."""
    if not _VISION_OK:
        return []
    path = Path(image_path)
    if not path.exists():
        return []

    url = NSURL.fileURLWithPath_(str(path.resolve()))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    out: list[tuple[str, float, tuple, Any]] = []

    def _completion(request, error):  # noqa: ANN001
        if error is not None:
            return
        for obs in request.results() or []:
            candidates = obs.topCandidates_(1)
            if not candidates:
                continue
            cand = candidates[0]
            out.append((str(cand.string()), float(cand.confidence()),
                        _vision_box(obs.boundingBox()), cand))

    request = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(
        _completion
    )
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)

    success, err = handler.performRequests_error_([request], None)
    if not success and err is not None:
        return []
    return out


def recognize_text(image_path: str) -> list[TextRegion]:
    """Run OCR on a PNG/JPEG file; returns line regions (normalized, top-left origin)."""
    return [TextRegion(text, conf, *box) for text, conf, box, _ in _observations(image_path)]


@dataclass
class TextHit:
    """One occurrence of a searched string: the box of the match itself."""

    line: str            # the whole recognized line (what the control says)
    match: str           # the matched characters as recognized
    region: TextRegion   # normalized box of the MATCH, not the whole line
    start: int           # character offset of the match in the line


def _norm_text(s: str) -> str:
    return " ".join(str(s or "").replace("…", "...").split()).lower()


def proportional_box(line: TextRegion, start: int, length: int) -> TextRegion:
    """Estimate a substring's box by character share (when Vision can't say)."""
    n = max(len(line.text), 1)
    x = line.x + line.w * (start / n)
    w = line.w * (max(length, 1) / n)
    return TextRegion(line.text[start:start + length], line.confidence, x, line.y, w, line.h)


def _range_box(cand: Any, start: int, length: int) -> tuple | None:
    """Vision's own box for a character range of a candidate, when it gives one."""
    try:
        res = cand.boundingBoxForRange_error_((start, length), None)
        rect = res[0] if isinstance(res, tuple) else res
        if rect is None:
            return None
        return _vision_box(rect.boundingBox())
    except Exception:  # noqa: BLE001
        return None


def find_in_lines(lines: list[tuple[TextRegion, Any]], needle: str) -> list[TextHit]:
    """Every occurrence of needle (case/space-insensitive) in recognized lines."""
    q = _norm_text(needle)
    hits: list[TextHit] = []
    if not q:
        return hits
    for region, cand in lines:
        text = region.text
        low = text.lower()
        # Character offsets are only trustworthy when lowercasing kept the length.
        pos = low.find(q) if len(low) == len(text) else -1
        if pos < 0:
            if q in _norm_text(text):   # found only after folding spaces/ellipses
                hits.append(TextHit(text, text, region, 0))
            continue
        while pos >= 0:
            box = _range_box(cand, pos, len(q)) if cand is not None else None
            sub = (TextRegion(text[pos:pos + len(q)], region.confidence, *box) if box
                   else proportional_box(region, pos, len(q)))
            hits.append(TextHit(text, text[pos:pos + len(q)], sub, pos))
            pos = low.find(q, pos + 1)
    return hits


def rank_hits(hits: list[TextHit], needle: str) -> list[TextHit]:
    """Best first: whole-line match, then whole-word match, then any substring;
    ties in reading order (top to bottom, left to right)."""
    q = _norm_text(needle)

    def tier(h: TextHit) -> int:
        if _norm_text(h.line) == q:
            return 0
        line = h.line.lower()
        before = line[h.start - 1] if 0 < h.start <= len(line) else " "
        after_i = h.start + len(h.match)
        after = line[after_i] if after_i < len(line) else " "
        if not before.isalnum() and not after.isalnum():
            return 1
        return 2

    return sorted(hits, key=lambda h: (tier(h), round(h.region.y, 3), h.region.x))


def find_text(image_path: str, needle: str) -> list[TextHit]:
    """OCR the image and return ranked occurrences of needle (normalized boxes)."""
    lines = [(TextRegion(text, conf, *box), cand)
             for text, conf, box, cand in _observations(image_path)]
    return rank_hits(find_in_lines(lines, needle), needle)


def regions_to_points(regions: list[TextRegion], image_path: str) -> list[TextRegion] | None:
    """Normalized OCR boxes → global screen points, for screenshots Aether took.

    Uses the capture's display geometry, so the result is click-ready on Retina
    and secondary displays. None when the image has no registered geometry.
    """
    from . import screen

    cap = screen.capture_info(image_path)
    if cap is None or not cap.display.known:
        return None
    out = []
    for r in regions:
        x0, y0 = cap.normalized_to_points(r.x, r.y)
        x1, y1 = cap.normalized_to_points(r.x + r.w, r.y + r.h)
        out.append(TextRegion(text=r.text, confidence=r.confidence,
                              x=x0, y=y0, w=x1 - x0, h=y1 - y0))
    return out


def _format_regions(
    regions: list[TextRegion],
    w: int,
    h: int,
    limit: int = 40,
    image_path: str | None = None,
) -> str:
    if not regions:
        return "No text detected." if _VISION_OK else "OCR unavailable (install pyobjc-framework-Vision)."
    points = regions_to_points(regions, image_path) if image_path else None
    if points is not None:
        lines = [r.describe(unit="pt") for r in points[:limit]]
        more = f"\n…({len(regions) - limit} more)" if len(regions) > limit else ""
        return (f"OCR found {len(regions)} regions (coordinates are screen points; "
                "click(x, y) at a box's center hits it):\n" + "\n".join(lines) + more)
    if w > 0 and h > 0:
        scaled = scale_regions_to_pixels(regions, w, h)
        lines = [r.describe(pixel_coords=True) for r in scaled[:limit]]
    else:
        lines = [r.describe() for r in regions[:limit]]
    more = f"\n…({len(regions) - limit} more)" if len(regions) > limit else ""
    return f"OCR found {len(regions)} regions:\n" + "\n".join(lines) + more


def recognize_text_formatted(image_path: str, limit: int = 40) -> str:
    """OCR with a compact string for LLM context."""
    regions = recognize_text(image_path)
    w, h = image_dimensions(image_path)
    return _format_regions(regions, w, h, limit, image_path=image_path)


def recognize(image_path: str) -> tuple[str, list[TextRegion], tuple[int, int]]:
    """Single OCR pass: returns (formatted_string, raw_regions, (w, h))."""
    regions = recognize_text(image_path)
    w, h = image_dimensions(image_path)
    return _format_regions(regions, w, h, image_path=image_path), regions, (w, h)


def scale_region_to_pixels(
    region: TextRegion,
    image_width: int,
    image_height: int,
) -> TextRegion:
    """Scale normalized 0–1 top-left coords to pixel space (Retina-aware)."""
    return TextRegion(
        text=region.text,
        confidence=region.confidence,
        x=region.x * image_width,
        y=region.y * image_height,
        w=region.w * image_width,
        h=region.h * image_height,
    )


def scale_regions_to_pixels(
    regions: list[TextRegion],
    image_width: int,
    image_height: int,
) -> list[TextRegion]:
    if image_width <= 0 or image_height <= 0:
        return list(regions)
    return [
        scale_region_to_pixels(r, image_width, image_height) for r in regions
    ]


def image_dimensions(image_path: str) -> tuple[int, int]:
    """Return (width, height) in pixels for a PNG/JPEG file."""
    if not _VISION_OK:
        return (0, 0)
    path = Path(image_path)
    if not path.exists():
        return (0, 0)
    url = NSURL.fileURLWithPath_(str(path.resolve()))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    w = int(handler.extent().size.width)
    h = int(handler.extent().size.height)
    if w > 0 and h > 0:
        return (w, h)
    try:
        from Quartz import CGImageSourceCopyPropertiesAtIndex, CGImageSourceCreateWithURL
        src = CGImageSourceCreateWithURL(url, None)
        if src is None:
            return (0, 0)
        props = CGImageSourceCopyPropertiesAtIndex(src, 0, None)
        if props is None:
            return (0, 0)
        pw = int(props.get("PixelWidth", 0))
        ph = int(props.get("PixelHeight", 0))
        return (pw, ph)
    except Exception:
        return (0, 0)


def regions_to_dicts(
    regions: list[TextRegion],
    *,
    image_path: str | None = None,
    scale_pixels: bool = True,
) -> list[dict[str, Any]]:
    """Serialize OCR regions; optionally scale normalized coords to pixels."""
    scaled = list(regions)
    if scale_pixels and image_path:
        w, h = image_dimensions(image_path)
        if w > 0 and h > 0:
            scaled = scale_regions_to_pixels(regions, w, h)
    points = regions_to_points(regions, image_path) if image_path else None
    out = []
    for i, r in enumerate(scaled):
        d: dict[str, Any] = {
            "text": r.text,
            "confidence": r.confidence,
            "x": r.x,
            "y": r.y,
            "w": r.w,
            "h": r.h,
            "normalized": not (scale_pixels and image_path),
        }
        if points is not None:
            p = points[i]
            # Global screen points: pass (screen_x + screen_w/2, …) to click().
            d.update(screen_x=p.x, screen_y=p.y, screen_w=p.w, screen_h=p.h)
        out.append(d)
    return out


def classify_screen_content(
    regions: list[TextRegion],
    image_w: int,
    image_h: int,
    *,
    min_conf: float = 0.3,
    char_threshold: int = 200,
    coverage_threshold: float = 0.05,
) -> dict[str, Any]:
    """Classify a screen as text_heavy / sparse / graphical / empty / unknown.

    `regions` are normalized (0–1) TextRegions as returned by recognize_text.
    Pure function — no I/O. Filters low-confidence boxes (Vision emits spurious
    boxes on graphics). 'unknown' when image dims are unavailable.
    """
    kept = [r for r in regions if r.confidence >= min_conf]
    region_count = len(kept)
    char_count = sum(len(r.text) for r in kept)
    mean_conf = (sum(r.confidence for r in kept) / region_count) if region_count else 0.0
    coverage = min(sum(max(r.w, 0.0) * max(r.h, 0.0) for r in kept), 1.0)

    base = {
        "char_count": char_count,
        "text_coverage": round(coverage, 4),
        "region_count": region_count,
        "mean_confidence": round(mean_conf, 3),
    }
    if image_w <= 0 or image_h <= 0:
        return {"label": "unknown", "score": 0.0, **base}
    if region_count == 0 or char_count == 0:
        return {"label": "empty", "score": 0.0, **base}

    score = min(1.0, (char_count / 400.0) * 0.6 + coverage * 0.4)
    if char_count >= char_threshold and coverage >= coverage_threshold and mean_conf >= 0.4:
        label = "text_heavy"
    elif coverage < 0.02 and char_count < 60:
        label = "graphical"
    else:
        label = "sparse"
    return {"label": label, "score": round(score, 3), **base}
