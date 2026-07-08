"""Anthropic computer-use API as a last-resort vision effector (Phase 5).

One call per step: screenshot → model proposes ONE action → we execute it via
CGEvent. The agent loop provides iteration/verification, so this stays a
single-action tool rather than an autonomous sub-loop. Gated by
``beta.computer_use_api`` (off by default) — it sends screenshots to Anthropic.
"""
from __future__ import annotations

import base64
import logging
import re
import subprocess
import tempfile
from typing import Any

import httpx

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
BETA_HEADER = "computer-use-2025-01-24"
DEFAULT_MODEL = "claude-sonnet-5"
MAX_IMG_WIDTH = 1280


def _resize_and_dims(path: str) -> tuple[str, int, int]:
    """Downscale screenshot to <=MAX_IMG_WIDTH via sips; return (path, w, h)."""
    out = subprocess.run(  # noqa: S603
        ["sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout
    w = int(re.search(r"pixelWidth: (\d+)", out).group(1))
    h = int(re.search(r"pixelHeight: (\d+)", out).group(1))
    if w > MAX_IMG_WIDTH:
        subprocess.run(  # noqa: S603
            ["sips", "--resampleWidth", str(MAX_IMG_WIDTH), path],
            capture_output=True, timeout=10, check=True,
        )
        h = round(h * MAX_IMG_WIDTH / w)
        w = MAX_IMG_WIDTH
    return path, w, h


def _display_points() -> tuple[float, float]:
    """Main display size in points (CGEvent coordinate space)."""
    try:
        import Quartz
        b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        return float(b.size.width), float(b.size.height)
    except Exception:
        return (1440.0, 900.0)


def _capture_main_display() -> str:
    """Screenshot the MAIN display only, so image pixels map to CGMainDisplayID
    bounds. The shared capture_to_file() stitches all displays on multi-monitor
    setups, which would break the pixel→point scaling below.
    """
    fd, path = tempfile.mkstemp(suffix=".png", prefix="aether-cu-")
    import os
    os.close(fd)
    # -D 1 = main display. ponytail: assumes main is index 1 (true on typical
    # setups); per-display targeting is the upgrade path if it ever isn't.
    subprocess.run(  # noqa: S603
        ["screencapture", "-x", "-D", "1", path],
        capture_output=True, timeout=15, check=True,
    )
    return path


def computer_use_step(instruction: str, api_key: str,
                      model: str = DEFAULT_MODEL) -> str:
    """Screenshot → one model-proposed action → execute. Returns description."""
    from . import input as kbd

    path = _capture_main_display()
    path, img_w, img_h = _resize_and_dims(path)
    img_b64 = base64.b64encode(open(path, "rb").read()).decode()

    resp = httpx.post(
        API_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": BETA_HEADER,
        },
        json={
            "model": model,
            "max_tokens": 1024,
            "tools": [{
                "type": "computer_20250124",
                "name": "computer",
                "display_width_px": img_w,
                "display_height_px": img_h,
            }],
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text":
                        f"{instruction}\n\nPropose exactly ONE next action."},
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/png",
                        "data": img_b64}},
                ],
            }],
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    data = resp.json()

    usage = data.get("usage") or {}
    try:
        from ..core.metrics import MetricsCollector
        MetricsCollector.get().record_llm_usage(
            "anthropic", usage.get("input_tokens"), usage.get("output_tokens"),
            model=model)
    except Exception:  # noqa: BLE001
        pass

    action_block = next(
        (b for b in data.get("content", []) if b.get("type") == "tool_use"), None)
    text = " ".join(b.get("text", "") for b in data.get("content", [])
                    if b.get("type") == "text").strip()
    if action_block is None:
        return f"Computer-use model proposed no action. It said: {text[:400]}"

    inp: dict[str, Any] = action_block.get("input", {})
    action = inp.get("action", "")
    # Model coordinates are in resized-image space → scale to display points.
    disp_w, disp_h = _display_points()
    sx, sy = disp_w / img_w, disp_h / img_h

    def _pt() -> tuple[float, float]:
        cx, cy = inp.get("coordinate", (0, 0))
        return float(cx) * sx, float(cy) * sy

    from . import executor
    with executor.HID_LOCK:
        if action in ("left_click", "right_click", "double_click"):
            x, y = _pt()
            kbd.click(x, y,
                      button="right" if action == "right_click" else "left",
                      count=2 if action == "double_click" else 1)
            did = f"{action} at ({int(x)},{int(y)})"
        elif action == "type":
            kbd.type_text(inp.get("text", ""))
            did = f"typed {len(inp.get('text', ''))} chars"
        elif action == "key":
            key = str(inp.get("text", "")).split("+")[-1]
            mods = str(inp.get("text", "")).split("+")[:-1]
            kbd.press_key(key, modifiers=mods or None)
            did = f"pressed {inp.get('text', '')}"
        elif action == "screenshot":
            did = "requested another screenshot (call analyze_screen)"
        else:
            return (f"Computer-use proposed unsupported action '{action}' "
                    f"({inp}). It said: {text[:300]}")
    reason = f" Reasoning: {text[:200]}" if text else ""
    return f"Computer-use executed: {did}.{reason}"
