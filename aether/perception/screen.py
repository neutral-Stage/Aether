"""Screen capture that knows its own geometry.

Every screenshot Aether takes is one display, and is registered with the
display it shows (global top-left points), its pixel size and the backing
scale. That metadata is what makes coordinates trustworthy:

- ``Capture.to_points(px, py)`` turns image pixels (from OCR or a vision model)
  into the global screen points that CGEvent clicks use — correct on Retina
  displays (2 px per point) and on secondary displays (non-zero origin).
- ``image_label(path)`` is the text sent to a model before the image: pixel
  size, which display, where the cursor is (Clicky / clicky-windows pattern).

Backends, best first:
1. The Swift app's ScreenCaptureKit endpoint (``GET /capture`` on the native
   effector server) — excludes Aether's own HUD/overlay windows so the model
   never sees them, and resizes to an exact pixel size.
2. ``screencapture -x -D <n>`` + ``sips -Z`` for resizing (CLI/dev fallback).
"""
from __future__ import annotations

import base64
import logging
import os
import struct
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path

log = logging.getLogger(__name__)
_warned_capture = False

# Long-edge cap for images sent to a model. Tuned per machine by
# scripts/calibrate_grounding.py (see grounding_settings()).
DEFAULT_MODEL_MAX_EDGE = 1600


@dataclass(frozen=True)
class DisplayInfo:
    """One display in global screen points (top-left origin, main at 0,0)."""

    index: int                 # 1-based, as `screencapture -D` numbers displays
    display_id: int            # CGDirectDisplayID (0 when unknown)
    x: float
    y: float
    width: float               # points
    height: float              # points
    scale: float = 1.0         # backing pixels per point
    is_main: bool = True
    known: bool = True         # False when geometry could not be read

    def contains(self, gx: float, gy: float) -> bool:
        return (self.x <= gx < self.x + self.width) and (self.y <= gy < self.y + self.height)


_FALLBACK_DISPLAY = DisplayInfo(1, 0, 0.0, 0.0, 0.0, 0.0, 1.0, True, known=False)


@dataclass(frozen=True)
class Capture:
    """A saved screenshot plus the geometry needed to map its pixels to points."""

    path: str
    display: DisplayInfo
    pixel_width: int
    pixel_height: int
    display_count: int = 1
    cursor_on_display: bool = False
    source: str = "screencapture"   # "native" (ScreenCaptureKit, own UI hidden) | "screencapture"

    @property
    def px_per_pt_x(self) -> float:
        if self.display.known and self.display.width > 0 and self.pixel_width > 0:
            return self.pixel_width / self.display.width
        return self.display.scale or 1.0

    @property
    def px_per_pt_y(self) -> float:
        if self.display.known and self.display.height > 0 and self.pixel_height > 0:
            return self.pixel_height / self.display.height
        return self.display.scale or 1.0

    def to_points(self, px: float, py: float) -> tuple[float, float]:
        """Image pixel → global screen point (what click(x, y) expects)."""
        return (self.display.x + px / self.px_per_pt_x,
                self.display.y + py / self.px_per_pt_y)

    def to_pixels(self, gx: float, gy: float) -> tuple[float, float]:
        """Global screen point → image pixel."""
        return ((gx - self.display.x) * self.px_per_pt_x,
                (gy - self.display.y) * self.px_per_pt_y)

    def normalized_to_points(self, nx: float, ny: float) -> tuple[float, float]:
        """0–1 image coordinates (Vision OCR) → global screen points."""
        return self.to_points(nx * self.pixel_width, ny * self.pixel_height)

    def label(self) -> str:
        d = self.display
        where = (f"display {d.index} of {self.display_count}"
                 if self.display_count > 1 else "the screen")
        cursor = " The mouse cursor is on this display." if (
            self.cursor_on_display and self.display_count > 1) else ""
        parts = [f"[Screenshot of {where}: {self.pixel_width}x{self.pixel_height} pixels."]
        if d.known and d.width > 0:
            parts.append(
                f" It covers screen points x {d.x:.0f}–{d.x + d.width:.0f}, "
                f"y {d.y:.0f}–{d.y + d.height:.0f} "
                f"({self.px_per_pt_x:.2f} image pixels per point).")
        parts.append(cursor)
        if self.source == "native":
            parts.append(" Aether's own windows are hidden.")
        parts.append(" Give image coordinates in this image's pixels.]")
        return "".join(parts)


# path -> Capture, bounded
_CAPTURES: OrderedDict[str, Capture] = OrderedDict()
_CAPTURES_MAX = 64
_captures_lock = threading.Lock()


def _register(cap: Capture) -> Capture:
    with _captures_lock:
        _CAPTURES[cap.path] = cap
        _CAPTURES.move_to_end(cap.path)
        while len(_CAPTURES) > _CAPTURES_MAX:
            _CAPTURES.popitem(last=False)
    return cap


def capture_info(path: str | None) -> Capture | None:
    """Geometry for a screenshot Aether took (None for foreign images)."""
    if not path:
        return None
    with _captures_lock:
        return _CAPTURES.get(str(path))


def image_label(path: str | None) -> str:
    cap = capture_info(path)
    return cap.label() if cap else ""


# ---------------------------------------------------------------- geometry


def list_displays() -> list[DisplayInfo]:
    """Active displays, main first (matching `screencapture -D` numbering)."""
    try:
        import Quartz
    except Exception:  # noqa: BLE001 — non-macOS / no pyobjc
        return [_FALLBACK_DISPLAY]
    try:
        err, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
        if err != 0 or not count:
            return [_FALLBACK_DISPLAY]
        main = Quartz.CGMainDisplayID()
        ordered = sorted(list(ids)[:count], key=lambda d: 0 if d == main else 1)
        out: list[DisplayInfo] = []
        for i, did in enumerate(ordered, start=1):
            b = Quartz.CGDisplayBounds(did)
            w, h = float(b.size.width), float(b.size.height)
            scale = 1.0
            mode = Quartz.CGDisplayCopyDisplayMode(did)
            if mode is not None and w > 0:
                pw = Quartz.CGDisplayModeGetPixelWidth(mode)
                if pw:
                    scale = float(pw) / w
            out.append(DisplayInfo(i, int(did), float(b.origin.x), float(b.origin.y),
                                   w, h, scale, did == main, True))
        return out or [_FALLBACK_DISPLAY]
    except Exception as e:  # noqa: BLE001
        log.debug("list_displays failed: %s", e)
        return [_FALLBACK_DISPLAY]


def cursor_position() -> tuple[float, float] | None:
    try:
        import Quartz

        loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        return float(loc.x), float(loc.y)
    except Exception:  # noqa: BLE001
        return None


def display_for_point(gx: float, gy: float,
                      displays: list[DisplayInfo] | None = None) -> DisplayInfo:
    displays = displays or list_displays()
    for d in displays:
        if d.contains(gx, gy):
            return d
    return next((d for d in displays if d.is_main), displays[0])


def default_display(displays: list[DisplayInfo] | None = None) -> DisplayInfo:
    """The display holding the frontmost window, else the cursor, else main."""
    displays = displays or list_displays()
    try:
        from . import accessibility as ax

        frame = ax.focused_window_frame()
    except Exception:  # noqa: BLE001
        frame = None
    if frame:
        x, y, w, h = frame
        return display_for_point(x + w / 2.0, y + h / 2.0, displays)
    cur = cursor_position()
    if cur:
        return display_for_point(cur[0], cur[1], displays)
    return next((d for d in displays if d.is_main), displays[0])


def _pick_display(display: int | DisplayInfo | None,
                  displays: list[DisplayInfo]) -> DisplayInfo:
    if isinstance(display, DisplayInfo):
        return display
    if isinstance(display, int):
        for d in displays:
            if d.index == display:
                return d
    return default_display(displays)


# ---------------------------------------------------------------- image files


def png_size(path: str) -> tuple[int, int]:
    """Pixel size from a PNG header (no image library needed)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
            w, h = struct.unpack(">II", head[16:24])
            return int(w), int(h)
    except OSError:
        pass
    return (0, 0)


def _sips_resize(path: str, max_edge: int) -> tuple[int, int]:
    """Resample in place so the long edge is max_edge; returns the new size."""
    subprocess.run(  # noqa: S603
        ["sips", "-Z", str(int(max_edge)), path],
        capture_output=True, timeout=15, check=True,
    )
    return png_size(path)


def _temp_png(prefix: str = "aether-screen-") -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    fd, path = tempfile.mkstemp(suffix=".png", prefix=f"{prefix}{ts}-")
    os.close(fd)
    return path


def resized_copy(path: str, max_edge: int) -> str:
    """A copy of a registered screenshot with its long edge capped at max_edge.

    OCR wants native resolution; models want a bounded image. The copy keeps
    the display geometry, so its pixels still map to the same screen points.
    Returns the original path when no resize is needed or possible.
    """
    cap = capture_info(path)
    w, h = (cap.pixel_width, cap.pixel_height) if cap else png_size(path)
    if not max_edge or max(w, h) <= max_edge:
        return path
    out = _temp_png("aether-model-")
    try:
        Path(out).write_bytes(Path(path).read_bytes())
        nw, nh = _sips_resize(out, max_edge)
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("resize failed (%s); sending the original", e)
        return path
    if cap:
        _register(replace(cap, path=out, pixel_width=nw, pixel_height=nh))
    return out


# ---------------------------------------------------------------- capture

_native_ok_until = 0.0
_native_fail_until = 0.0


def _capture_native(disp: DisplayInfo, max_edge: int | None) -> dict | None:
    """Ask the Swift app (ScreenCaptureKit) for the display; None if unavailable."""
    global _native_ok_until, _native_fail_until
    backend = os.getenv("AETHER_CAPTURE_BACKEND", "auto").lower()
    if backend == "cli":
        return None
    now = time.monotonic()
    if now < _native_fail_until:
        return None
    try:
        from ..ipc import native_effector

        if now >= _native_ok_until and not native_effector.available():
            _native_fail_until = now + 30.0
            return None
        res = native_effector.capture(display_id=disp.display_id or None,
                                      max_edge=max_edge or 0)
        _native_ok_until = now + 30.0
        return res
    except Exception as e:  # noqa: BLE001 — fall back to screencapture
        log.debug("native capture unavailable: %s", e)
        _native_fail_until = now + 30.0
        return None


def _capture_cli(disp: DisplayInfo, max_edge: int | None, path: str | None) -> tuple[str, int, int]:
    path = path or _temp_png()
    args = ["screencapture", "-x"]
    if disp.known:
        args += ["-D", str(disp.index)]
    subprocess.run([*args, path], check=True, timeout=15)  # noqa: S603
    w, h = png_size(path)
    if max_edge and max(w, h) > max_edge:
        w, h = _sips_resize(path, max_edge)
    return path, w, h


def capture(display: int | DisplayInfo | None = None, *,
            max_edge: int | None = None, path: str | None = None) -> Capture:
    """Capture one display and register its geometry. Raises on failure."""
    displays = list_displays()
    disp = _pick_display(display, displays)
    cur = cursor_position()
    on_display = bool(cur and disp.known and disp.contains(*cur))

    native = _capture_native(disp, max_edge)
    if native and native.get("path"):
        frame = native.get("frame") or []
        if len(frame) == 4:
            disp = replace(disp, x=float(frame[0]), y=float(frame[1]),
                           width=float(frame[2]), height=float(frame[3]),
                           scale=float(native.get("scale") or disp.scale), known=True)
        src_path = str(native["path"])
        if path:
            Path(path).write_bytes(Path(src_path).read_bytes())
            src_path = path
        w = int(native.get("width") or 0)
        h = int(native.get("height") or 0)
        if not (w and h):
            w, h = png_size(src_path)
        return _register(Capture(src_path, disp, w, h, len(displays), on_display, "native"))

    out, w, h = _capture_cli(disp, max_edge, path)
    return _register(Capture(out, disp, w, h, len(displays), on_display, "screencapture"))


def capture_to_file(path: str | None = None, *, display: int | DisplayInfo | None = None,
                    max_edge: int | None = None) -> str:
    """Capture a display (default: the frontmost window's) and return the PNG path."""
    return capture(display, max_edge=max_edge, path=path).path


def try_capture_to_file(path: str | None = None, **kwargs) -> str | None:  # noqa: ANN003
    """Capture the screen, returning None (not raising) on failure.

    Degrades gracefully when Screen Recording permission is denied or the
    capture times out, so the vision tier falls back to AX-only context.
    """
    global _warned_capture
    try:
        return capture_to_file(path, **kwargs)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        if not _warned_capture:
            log.warning("Screen capture failed (Screen Recording permission?): %s", e)
            _warned_capture = True
        return None


def capture_base64() -> str:
    """Capture the screen and return base64 PNG (for sending to a vision model)."""
    path = capture_to_file()
    data = Path(path).read_bytes()
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return base64.b64encode(data).decode("ascii")


# ---------------------------------------------------------------- grounding config


def grounding_settings() -> dict:
    """Image size + coordinate convention for vision models.

    Precedence: AETHER_IMAGE_MAX_EDGE env → the machine's calibration file
    (<data_dir>/grounding_calibration.json, written by
    scripts/calibrate_grounding.py) → router.yaml `grounding:` → defaults.
    coord_space is "pixels" (image pixels), "norm1000" (0–1000 grid) or "auto".
    """
    settings: dict = {"max_image_edge": DEFAULT_MODEL_MAX_EDGE, "coord_space": "auto"}
    try:
        from ..core.config import ROOT
        import yaml

        raw = yaml.safe_load((ROOT / "configs" / "router.yaml").read_text()) or {}
        settings.update({k: v for k, v in (raw.get("grounding") or {}).items() if v is not None})
    except Exception:  # noqa: BLE001
        pass
    try:
        import json

        from ..core.paths import data_dir

        cal = data_dir() / "grounding_calibration.json"
        if cal.exists():
            data = json.loads(cal.read_text())
            for key in ("max_image_edge", "coord_space"):
                if data.get(key):
                    settings[key] = data[key]
    except Exception:  # noqa: BLE001
        pass
    env = os.getenv("AETHER_IMAGE_MAX_EDGE")
    if env and env.isdigit():
        settings["max_image_edge"] = int(env)
    settings["max_image_edge"] = int(settings.get("max_image_edge") or DEFAULT_MODEL_MAX_EDGE)
    return settings


if __name__ == "__main__":
    c = capture()
    print("Saved screenshot:", c.path)
    print(c.label())
