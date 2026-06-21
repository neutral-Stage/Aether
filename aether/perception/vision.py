"""Vision fallback — screenshot analysis when AX is insufficient (§6.2)."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from . import ocr
from . import screen as screen_cap


def analyze_screen(
    image_path: str | None = None,
    *,
    use_cloud: bool = False,
    cloud_analyze_fn: Any = None,
) -> dict[str, Any]:
    """Capture or analyze a screenshot.

    Runs OCR once, classifies the screen, and only calls the cloud VLM when the
    screen is NOT text-heavy (auto OCR-only saves latency/cost on dense text).
    Returns dict with path, ocr_formatted, regions, content_class, and optional
    cloud_analysis.
    """
    path = image_path or screen_cap.try_capture_to_file()
    if path is None:
        return {
            "path": None,
            "ocr_formatted": "Screen capture unavailable (permission?).",
            "regions": [],
            "region_count": 0,
            "content_class": "unknown",
        }
    formatted, regions, (w, h) = ocr.recognize(path)
    content = ocr.classify_screen_content(regions, w, h)
    result: dict[str, Any] = {
        "path": path,
        "ocr_formatted": formatted,
        "regions": ocr.regions_to_dicts(regions, image_path=path, scale_pixels=True),
        "region_count": len(regions),
        "content_class": content["label"],
        "content": content,
    }
    text_heavy = content["label"] == "text_heavy"
    if use_cloud and cloud_analyze_fn is not None and not text_heavy:
        try:
            result["cloud_analysis"] = cloud_analyze_fn(path)
        except Exception as e:  # noqa: BLE001
            result["cloud_analysis_error"] = str(e)
    return result


def image_to_base64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")
