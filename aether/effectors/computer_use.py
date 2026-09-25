"""Anthropic computer use as a last-resort vision effector.

One call per step: screenshot → the model proposes ONE action → we execute it
via CGEvent. The agent loop provides iteration/verification, so this stays a
single-action tool rather than an autonomous sub-loop. Gated by
``beta.computer_use_api`` (off by default) — it sends screenshots to Anthropic.

Also offers ``locate(label)``: ask the model where a UI element is and return
its screen point WITHOUT executing anything (a high-accuracy grounder used to
refine pointer targets).

Tool version: ``computer_20251124`` with beta ``computer-use-2025-11-24`` on
Claude Sonnet 5 (Claude Opus 5.5 accepts only the newer computer toolset, so
this module pins Sonnet 5).
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5"
TOOL_TYPE = "computer_20251124"
BETA = "computer-use-2025-11-24"
# Sonnet 5 handles up to 2576 px; 1366 px is the recommended cost/accuracy balance.
MAX_IMG_EDGE = 1366


def _screenshot():  # noqa: ANN202 — returns screen.Capture
    """Capture the working display at computer-use resolution."""
    from ..perception import screen

    return screen.capture(max_edge=MAX_IMG_EDGE)


def _call(api_key: str, model: str, cap, text: str, max_tokens: int = 1024):  # noqa: ANN001, ANN202
    import anthropic

    img_b64 = base64.b64encode(Path(cap.path).read_bytes()).decode()
    client = anthropic.Anthropic(api_key=api_key, timeout=60.0)
    resp = client.beta.messages.create(
        model=model,
        max_tokens=max_tokens,
        betas=[BETA],
        tools=[{
            "type": TOOL_TYPE,
            "name": "computer",
            "display_width_px": cap.pixel_width,
            "display_height_px": cap.pixel_height,
        }],
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/png", "data": img_b64}},
            ],
        }],
    )
    try:
        from ..core.metrics import MetricsCollector

        usage = getattr(resp, "usage", None)
        MetricsCollector.get().record_llm_usage(
            "anthropic", getattr(usage, "input_tokens", None),
            getattr(usage, "output_tokens", None), model=model)
    except Exception:  # noqa: BLE001
        pass
    tool_use = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
    said = " ".join(getattr(b, "text", "") for b in resp.content
                    if getattr(b, "type", None) == "text").strip()
    return (dict(tool_use.input or {}) if tool_use is not None else None), said


def locate(label: str, api_key: str, model: str = DEFAULT_MODEL,
           cap=None) -> tuple[float, float] | None:  # noqa: ANN001
    """Screen point of a described UI element, or None. Never acts."""
    cap = cap or _screenshot()
    inp, _ = _call(api_key, model, cap,
                   f"Locate the '{label}' element on screen and move the mouse to it. "
                   "Do not click. If it is not visible, do not call the tool.")
    if not inp or "coordinate" not in inp:
        return None
    cx, cy = inp["coordinate"]
    return cap.to_points(float(cx), float(cy))


def computer_use_step(instruction: str, api_key: str,
                      model: str = DEFAULT_MODEL) -> str:
    """Screenshot → one model-proposed action → execute. Returns description."""
    from . import executor
    from . import input as kbd

    cap = _screenshot()
    inp, text = _call(api_key, model, cap,
                      f"{instruction}\n\nPropose exactly ONE next action.")
    if inp is None:
        return f"Computer-use model proposed no action. It said: {text[:400]}"

    action = str(inp.get("action", ""))

    def pt(key: str = "coordinate") -> tuple[float, float]:
        cx, cy = inp.get(key) or (0, 0)
        return cap.to_points(float(cx), float(cy))

    with executor.HID_LOCK:
        if action in ("left_click", "right_click", "double_click", "triple_click",
                      "middle_click"):
            x, y = pt()
            count = {"double_click": 2, "triple_click": 3}.get(action, 1)
            kbd.click(x, y, button="right" if action == "right_click" else "left",
                      count=count)
            did = f"{action} at ({int(x)},{int(y)})"
        elif action == "mouse_move":
            x, y = pt()
            kbd.move(x, y)
            did = f"moved the mouse to ({int(x)},{int(y)})"
        elif action == "left_click_drag":
            x0, y0 = pt("start_coordinate")
            x1, y1 = pt()
            kbd.drag(x0, y0, x1, y1)
            did = f"dragged ({int(x0)},{int(y0)}) → ({int(x1)},{int(y1)})"
        elif action == "scroll":
            x, y = pt()
            amount = int(inp.get("scroll_amount") or 3)
            direction = str(inp.get("scroll_direction") or "down")
            dy = {"down": -amount, "up": amount}.get(direction, 0)
            dx = {"left": amount, "right": -amount}.get(direction, 0)
            kbd.scroll(dx=dx, dy=dy, x=x, y=y)
            did = f"scrolled {direction} {amount} at ({int(x)},{int(y)})"
        elif action == "type":
            kbd.type_text(str(inp.get("text", "")))
            did = f"typed {len(str(inp.get('text', '')))} chars"
        elif action in ("key", "hold_key"):
            combo = [p for p in str(inp.get("text", "")).split("+") if p]
            if not combo:
                return "Computer-use proposed an empty key press."
            kbd.press_key(combo[-1], modifiers=combo[:-1] or None)
            did = f"pressed {inp.get('text', '')}"
        elif action == "wait":
            import time

            time.sleep(min(float(inp.get("duration") or 1.0), 5.0))
            did = "waited"
        elif action in ("screenshot", "zoom", "cursor_position"):
            did = f"requested {action} (call analyze_screen or get_screen_context)"
        else:
            return (f"Computer-use proposed unsupported action '{action}' "
                    f"({inp}). It said: {text[:300]}")
    reason = f" Reasoning: {text[:200]}" if text else ""
    return f"Computer-use executed: {did}.{reason}"
