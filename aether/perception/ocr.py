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

    def describe(self, *, pixel_coords: bool = False) -> str:
        unit = "px" if pixel_coords else "norm"
        return (f'"{self.text}" conf={self.confidence:.2f} '
                f'@({int(self.x)},{int(self.y)} {int(self.w)}x{int(self.h)} {unit})')


def available() -> bool:
    return _VISION_OK


def recognize_text(image_path: str) -> list[TextRegion]:
    """Run OCR on a PNG/JPEG file; returns text regions with bounding boxes."""
    if not _VISION_OK:
        return []
    path = Path(image_path)
    if not path.exists():
        return []

    url = NSURL.fileURLWithPath_(str(path.resolve()))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)

    results_holder: list[TextRegion] = []

    def _completion(request, error):  # noqa: ANN001
        if error is not None:
            return
        observations = request.results() or []
        for obs in observations:
            candidates = obs.topCandidates_(1)
            if not candidates:
                continue
            cand = candidates[0]
            text = str(cand.string())
            conf = float(cand.confidence())
            box = obs.boundingBox()  # normalized, origin bottom-left
            # Convert Vision normalized coords to screen-style top-left pixels
            # (caller may scale; we store normalized 0-1 for portability)
            x = float(box.origin.x)
            y = float(1.0 - box.origin.y - box.size.height)
            w = float(box.size.width)
            h = float(box.size.height)
            results_holder.append(TextRegion(text, conf, x, y, w, h))

    request = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(
        _completion
    )
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)

    success, err = handler.performRequests_error_([request], None)
    if not success and err is not None:
        return []

    return results_holder


def _format_regions(
    regions: list[TextRegion],
    w: int,
    h: int,
    limit: int = 40,
) -> str:
    if not regions:
        return "No text detected." if _VISION_OK else "OCR unavailable (install pyobjc-framework-Vision)."
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
    return _format_regions(regions, w, h, limit)


def recognize(image_path: str) -> tuple[str, list[TextRegion], tuple[int, int]]:
    """Single OCR pass: returns (formatted_string, raw_regions, (w, h))."""
    regions = recognize_text(image_path)
    w, h = image_dimensions(image_path)
    return _format_regions(regions, w, h), regions, (w, h)


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
    return [
        {
            "text": r.text,
            "confidence": r.confidence,
            "x": r.x,
            "y": r.y,
            "w": r.w,
            "h": r.h,
            "normalized": not (scale_pixels and image_path),
        }
        for r in scaled
    ]


def classify_screen_content(
    regions: list[TextRegion],
    image_w: int,
    image_h: int,
    *,
    min_conf: float = 0.3,
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
    if char_count >= 200 and coverage >= 0.05 and mean_conf >= 0.4:
        label = "text_heavy"
    elif coverage < 0.02 and char_count < 60:
        label = "graphical"
    else:
        label = "sparse"
    return {"label": label, "score": round(score, 3), **base}
