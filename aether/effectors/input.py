"""Synthetic mouse + keyboard via Core Graphics (CGEvent).

OS-level input that works in any app. Requires Accessibility permission.
"""
from __future__ import annotations

import time

try:
    from Quartz import (
        CGEventCreateMouseEvent, CGEventCreateKeyboardEvent, CGEventPost,
        CGEventKeyboardSetUnicodeString, CGEventSetFlags, CGEventSetIntegerValueField,
        kCGHIDEventTap, kCGEventLeftMouseDown, kCGEventLeftMouseUp,
        kCGEventRightMouseDown, kCGEventRightMouseUp, kCGEventMouseMoved,
        kCGMouseButtonLeft, kCGMouseButtonRight, kCGMouseEventClickState,
    )
    _OK = True
except Exception:  # pragma: no cover
    _OK = False

# Minimal US-layout virtual keycodes for special keys / shortcuts.
KEYCODES = {
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51,
    "escape": 27, "esc": 27, "left": 123, "right": 124, "down": 125, "up": 126,
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8,
    "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
}

_MOD_FLAGS = {
    "cmd": 1 << 20, "command": 1 << 20,
    "shift": 1 << 17,
    "option": 1 << 19, "alt": 1 << 19,
    "control": 1 << 18, "ctrl": 1 << 18,
}


def available() -> bool:
    return _OK


def _post(ev):
    if ev is not None:
        CGEventPost(kCGHIDEventTap, ev)


def move(x: float, y: float) -> None:
    _post(CGEventCreateMouseEvent(None, kCGEventMouseMoved, (x, y), kCGMouseButtonLeft))


def click(x: float, y: float, button: str = "left", count: int = 1) -> None:
    """Click at screen coordinates (single or double)."""
    if not _OK:
        raise RuntimeError("Quartz unavailable (run on macOS).")
    if button == "right":
        down, up, btn = kCGEventRightMouseDown, kCGEventRightMouseUp, kCGMouseButtonRight
    else:
        down, up, btn = kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGMouseButtonLeft

    move(x, y)
    for i in range(1, count + 1):
        d = CGEventCreateMouseEvent(None, down, (x, y), btn)
        u = CGEventCreateMouseEvent(None, up, (x, y), btn)
        if i > 1:  # encode multi-click so apps register a double-click
            CGEventSetIntegerValueField(d, kCGMouseEventClickState, i)
            CGEventSetIntegerValueField(u, kCGMouseEventClickState, i)
        _post(d)
        _post(u)
        time.sleep(0.02)


def double_click(x: float, y: float) -> None:
    click(x, y, count=2)


def type_text(text: str, per_char_delay: float = 0.005) -> None:
    """Type arbitrary unicode text via keyboard events."""
    if not _OK:
        raise RuntimeError("Quartz unavailable (run on macOS).")
    for ch in text:
        down = CGEventCreateKeyboardEvent(None, 0, True)
        up = CGEventCreateKeyboardEvent(None, 0, False)
        CGEventKeyboardSetUnicodeString(down, len(ch), ch)
        CGEventKeyboardSetUnicodeString(up, len(ch), ch)
        _post(down)
        _post(up)
        if per_char_delay:
            time.sleep(per_char_delay)


def press_key(key: str, modifiers: list[str] | None = None) -> None:
    """Press a named key (e.g. 'return', 'c') with optional modifiers."""
    if not _OK:
        raise RuntimeError("Quartz unavailable (run on macOS).")
    key = key.lower()
    if key not in KEYCODES:
        raise ValueError(f"Unknown key: {key!r}")
    flags = 0
    for m in (modifiers or []):
        flags |= _MOD_FLAGS.get(m.lower(), 0)
    code = KEYCODES[key]
    down = CGEventCreateKeyboardEvent(None, code, True)
    up = CGEventCreateKeyboardEvent(None, code, False)
    if flags:
        CGEventSetFlags(down, flags)
        CGEventSetFlags(up, flags)
    _post(down)
    _post(up)


def hotkey(*keys: str) -> None:
    """e.g. hotkey('cmd', 'c'). Last item is the key; the rest are modifiers."""
    if not keys:
        return
    *mods, key = keys
    press_key(key, modifiers=list(mods))
