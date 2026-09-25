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

# macOS virtual keycodes (Carbon kVK_*; ANSI letters/digits/punctuation are
# layout-positional, named keys are layout-independent). Keep in sync with the
# Swift InputController if it grows a keymap.
KEYCODES: dict[str, int] = {
    # letters
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8,
    "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
    # digits (top row)
    "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22, "7": 26, "8": 28,
    "9": 25, "0": 29,
    # punctuation
    "=": 24, "-": 27, "]": 30, "[": 33, "'": 39, ";": 41, "\\": 42, ",": 43,
    "/": 44, ".": 47, "`": 50,
    # named keys
    "return": 36, "tab": 48, "space": 49, "delete": 51, "escape": 53,
    "forward_delete": 117, "home": 115, "end": 119, "page_up": 116,
    "page_down": 121, "help": 114,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "command": 55, "shift": 56, "caps_lock": 57, "option": 58, "control": 59,
    "right_shift": 60, "right_option": 61, "right_control": 62, "function": 63,
    "volume_up": 72, "volume_down": 73, "mute": 74,
    # function keys
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98,
    "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111, "f13": 105,
    "f14": 107, "f15": 113, "f16": 106, "f17": 64, "f18": 79, "f19": 80, "f20": 90,
    # keypad
    "keypad_0": 82, "keypad_1": 83, "keypad_2": 84, "keypad_3": 85, "keypad_4": 86,
    "keypad_5": 87, "keypad_6": 88, "keypad_7": 89, "keypad_8": 91, "keypad_9": 92,
    "keypad_decimal": 65, "keypad_multiply": 67, "keypad_plus": 69,
    "keypad_clear": 71, "keypad_divide": 75, "keypad_enter": 76,
    "keypad_minus": 78, "keypad_equals": 81,
}

# Spellings models and people use for the same key.
KEY_ALIASES: dict[str, str] = {
    "enter": "return", "ret": "return", "newline": "return",
    "esc": "escape",
    "backspace": "delete", "back_space": "delete",
    "del": "forward_delete", "forwarddelete": "forward_delete", "fwd_delete": "forward_delete",
    "pageup": "page_up", "pgup": "page_up", "pagedown": "page_down", "pgdn": "page_down",
    "arrowleft": "left", "arrow_left": "left", "leftarrow": "left", "left_arrow": "left",
    "arrowright": "right", "arrow_right": "right", "rightarrow": "right", "right_arrow": "right",
    "arrowup": "up", "arrow_up": "up", "uparrow": "up", "up_arrow": "up",
    "arrowdown": "down", "arrow_down": "down", "downarrow": "down", "down_arrow": "down",
    "spacebar": "space", "space_bar": "space", " ": "space",
    "cmd": "command", "super": "command", "meta": "command",
    "opt": "option", "alt": "option", "ctrl": "control",
    "capslock": "caps_lock", "fn": "function",
    "minus": "-", "hyphen": "-", "dash": "-", "equal": "=", "equals": "=",
    "comma": ",", "period": ".", "dot": ".", "slash": "/", "backslash": "\\",
    "semicolon": ";", "quote": "'", "apostrophe": "'", "grave": "`", "backtick": "`",
    "left_bracket": "[", "leftbracket": "[", "right_bracket": "]", "rightbracket": "]",
    "volumeup": "volume_up", "volumedown": "volume_down",
}

_MOD_FLAGS = {
    # "super"/"meta" come from xdotool-style names (Anthropic computer use).
    "cmd": 1 << 20, "command": 1 << 20, "⌘": 1 << 20, "super": 1 << 20, "meta": 1 << 20,
    "shift": 1 << 17, "⇧": 1 << 17,
    "option": 1 << 19, "opt": 1 << 19, "alt": 1 << 19, "⌥": 1 << 19,
    "control": 1 << 18, "ctrl": 1 << 18, "⌃": 1 << 18,
    "fn": 1 << 23, "function": 1 << 23,
}


def keycode_for(key: str) -> int:
    """Resolve a key name ("Return", "PageDown", "F5", "cmd", "-") to a keycode."""
    raw = str(key or "")
    for k in (raw, raw.lower(), raw.strip().lower()):
        if k in KEYCODES:
            return KEYCODES[k]
        if k in KEY_ALIASES:
            return KEYCODES[KEY_ALIASES[k]]
    k = raw.strip().lower()
    if len(k) > 1:
        norm = k.replace(" ", "_").replace("-", "_")
        squashed = norm.replace("_", "")
        for name in (*KEYCODES, *KEY_ALIASES):
            if name == norm or name.replace("_", "") == squashed:
                return KEYCODES[KEY_ALIASES.get(name, name)]
    raise ValueError(
        f"Unknown key: {key!r}. Use type_text for literal characters; named keys "
        "include return, tab, space, escape, delete, forward_delete, arrows, "
        "home/end/page_up/page_down, f1–f20, digits and punctuation."
    )


def utf16_len(text: str) -> int:
    """Length in UTF-16 code units — what CGEventKeyboardSetUnicodeString expects."""
    return len(text.encode("utf-16-le")) // 2


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
        # Emoji and other non-BMP characters are two UTF-16 code units.
        n = utf16_len(ch)
        CGEventKeyboardSetUnicodeString(down, n, ch)
        CGEventKeyboardSetUnicodeString(up, n, ch)
        _post(down)
        _post(up)
        if per_char_delay:
            time.sleep(per_char_delay)


def press_key(key: str, modifiers: list[str] | None = None) -> None:
    """Press a named key (e.g. 'return', 'c') with optional modifiers."""
    if not _OK:
        raise RuntimeError("Quartz unavailable (run on macOS).")
    code = keycode_for(key)
    flags = 0
    for m in (modifiers or []):
        flag = _MOD_FLAGS.get(str(m).strip().lower())
        if flag is None:
            raise ValueError(f"Unknown modifier: {m!r} (use cmd, shift, option, control, fn)")
        flags |= flag
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
