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

    Returns dict with path, ocr_text, regions, and optional cloud_analysis.
    """
    path = image_path or screen_cap.capture_to_file()
    regions = ocr.recognize_text(path)
    result: dict[str, Any] = {
        "path": path,
        "ocr_formatted": ocr.recognize_text_formatted(path),
        "regions": ocr.regions_to_dicts(regions, image_path=path, scale_pixels=True),
        "region_count": len(regions),
    }
    if use_cloud and cloud_analyze_fn is not None:
        try:
            result["cloud_analysis"] = cloud_analyze_fn(path)
        except Exception as e:  # noqa: BLE001
            result["cloud_analysis_error"] = str(e)
    return result


def image_to_base64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")
