"""Find a described UI element with a vision model ("the gear icon next to Wi-Fi").

The rung of the targeting ladder after names, text and numbered marks: one
fresh screenshot, one question, one point. The model is the optional local
grounder when it is enabled and answering (``grounding.local_grounder`` in
configs/router.yaml: a UI-TARS-style model served by an OpenAI-compatible
server on this Mac), else the vision role. The point is refined once on a
native-resolution crop, then snapped to the accessibility element under it
when there is one, so the click lands on the control's center.
"""
from __future__ import annotations

import math
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

from . import accessibility as ax
from . import grounding, pointing, screen

LOCATE_SYSTEM = (
    "You find user-interface elements in screenshots. Reply with only JSON "
    '{"x": <int>, "y": <int>}: the center of the element, in the pixel coordinates of '
    'the image you were given. If it is not visible, reply {"x": null, "y": null}.')
REFINE_HALF_PT = 150.0
MAX_REFINE_SHIFT_PT = 120.0
CLICK_SNAP_PT = 10.0
_PROBE_TTL = 60.0
_probe_cache: dict[str, tuple[float, bool]] = {}


@dataclass
class Located:
    x: float
    y: float
    source: str                  # grounder | vision
    element: Any = None          # the accessibility element it snapped to, if any

    @property
    def element_label(self) -> str:
        el = self.element
        return (getattr(el, "title", "") or getattr(el, "value", "") or "") if el else ""


def grounder_settings() -> dict[str, Any]:
    try:
        import yaml

        from ..core.config import ROOT

        raw = yaml.safe_load((ROOT / "configs" / "router.yaml").read_text()) or {}
    except Exception:  # noqa: BLE001
        return {}
    return dict((raw.get("grounding") or {}).get("local_grounder") or {})


def _answering(base_url: str, timeout: float = 1.5) -> bool:
    """Is the local server up? (GET /models, cached for a minute)."""
    now = time.monotonic()
    hit = _probe_cache.get(base_url)
    if hit and now - hit[0] < _PROBE_TTL:
        return hit[1]
    ok = False
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=timeout) as r:  # noqa: S310
            ok = 200 <= r.status < 300
    except (OSError, ValueError):
        ok = False
    _probe_cache[base_url] = (now, ok)
    return ok


def local_grounder() -> tuple[Any, str] | None:
    """(client, coord_space) for the local grounder, or None when off or not running."""
    s = grounder_settings()
    if not s.get("enabled"):
        return None
    from ..core.providers import create_client, is_loopback_url

    base_url = str(s.get("base_url") or "http://127.0.0.1:8080/v1")
    if not is_loopback_url(base_url) or not _answering(base_url):
        return None
    client = create_client({"backend": "openai_compatible", "base_url": base_url,
                            "model": str(s.get("model") or ""), "max_tokens": 128,
                            "temperature": 0, "provider_label": "local_grounder"},
                           role_name="local_grounder")
    if client is None:
        return None
    return client, grounding.resolve_coord_space(str(s.get("coord_space") or "pixels"))


def _ask_point(client: Any, image_path: str, question: str) -> tuple[float, float] | None:
    from ..core.llm import image_block

    content = [{"type": "text", "text": screen.image_label(image_path)},
               image_block(image_path), {"type": "text", "text": question}]
    resp = client.step(LOCATE_SYSTEM, [{"role": "user", "content": content}], [])
    return grounding.parse_point(str(getattr(resp, "text", "") or ""))


def locate(description: str, client: Any, *, coord_space: str = "pixels",
           source: str = "vision", cap: screen.Capture | None = None, refine: bool = True,
           elements: list[Any] | None = None, max_edge: int | None = None) -> Located | None:
    """Screen point of the described element, or None when the model can't see it."""
    cap = cap or screen.capture()
    model_path = screen.resized_copy(cap.path, max_edge) if max_edge else cap.path
    model_cap = screen.capture_info(model_path) or cap
    pt = _ask_point(client, model_path, f"Where is: {description}?")
    if pt is None:
        return None
    gx, gy = grounding.to_screen_point(model_cap, pt[0], pt[1], coord_space)
    if refine:
        crop = screen.crop_around(cap, gx, gy, half_pt=REFINE_HALF_PT, scale=1.0)
        try:
            again = _ask_point(client, crop.path, f"This is a close crop. Where is the center "
                                                  f"of: {description}? Beware of similar-"
                                                  "looking neighbours.")
        except Exception:  # noqa: BLE001 — keep the first answer
            again = None
        if again is not None:
            rx, ry = again
            if coord_space == "norm1000" and crop.width and crop.height:
                rx, ry = rx / 1000.0 * crop.width, ry / 1000.0 * crop.height
            fx, fy = crop.to_points(rx, ry)
            if math.hypot(fx - gx, fy - gy) <= MAX_REFINE_SHIFT_PT:
                gx, gy = fx, fy
    if elements is None:
        try:
            elements = ax.read_tree(max_elements=300, capture_handles=False)
        except Exception:  # noqa: BLE001 — no accessibility: keep the point
            elements = []
    el = pointing.snap(gx, gy, elements, cap, radius=CLICK_SNAP_PT)
    if el is not None:
        return Located(el.x + el.w / 2.0, el.y + el.h / 2.0, source, el)
    return Located(gx, gy, source)
