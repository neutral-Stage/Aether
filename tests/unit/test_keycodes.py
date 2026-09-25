"""macOS virtual keycode table and key-name resolution."""
from __future__ import annotations

import string
from collections import Counter

import pytest

from aether.effectors import input as kbd


@pytest.mark.unit
class TestKeycodes:
    def test_escape_is_kvk_escape(self) -> None:
        # 27 is kVK_ANSI_Minus; the old table sent '-' for every Escape press.
        assert kbd.keycode_for("escape") == 53
        assert kbd.keycode_for("esc") == 53
        assert kbd.keycode_for("-") == 27

    def test_every_letter_and_digit_present(self) -> None:
        for ch in string.ascii_lowercase + string.digits:
            assert ch in kbd.KEYCODES, ch

    def test_codes_are_unique(self) -> None:
        dupes = [code for code, n in Counter(kbd.KEYCODES.values()).items() if n > 1]
        assert dupes == []

    def test_aliases_point_at_real_keys(self) -> None:
        for alias, target in kbd.KEY_ALIASES.items():
            assert target in kbd.KEYCODES, (alias, target)

    @pytest.mark.parametrize(("name", "code"), [
        ("Return", 36), ("enter", 36), ("Tab", 48), ("space", 49), (" ", 49),
        ("BackSpace", 51), ("delete", 51), ("del", 117), ("Page_Down", 121),
        ("page down", 121), ("PageUp", 116), ("home", 115), ("End", 119),
        ("ArrowUp", 126), ("left", 123), ("F1", 122), ("f12", 111), ("F20", 90),
        ("A", 0), ("7", 26), ("0", 29), ("\\", 42), ("comma", 43), ("super", 55),
    ])
    def test_resolution(self, name: str, code: int) -> None:
        assert kbd.keycode_for(name) == code

    def test_unknown_key_explains_itself(self) -> None:
        with pytest.raises(ValueError, match="type_text"):
            kbd.keycode_for("!")

    def test_utf16_length_counts_surrogate_pairs(self) -> None:
        assert kbd.utf16_len("a") == 1
        assert kbd.utf16_len("é") == 1
        assert kbd.utf16_len("😀") == 2


@pytest.mark.unit
def test_press_key_rejects_unknown_modifier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kbd, "_OK", True)
    with pytest.raises(ValueError, match="modifier"):
        kbd.press_key("c", modifiers=["hyper"])


@pytest.mark.unit
def test_press_key_posts_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[tuple] = []
    monkeypatch.setattr(kbd, "_OK", True)
    monkeypatch.setattr(kbd, "CGEventCreateKeyboardEvent",
                        lambda src, code, down: {"code": code, "down": down}, raising=False)
    monkeypatch.setattr(kbd, "CGEventSetFlags",
                        lambda ev, flags: ev.__setitem__("flags", flags), raising=False)
    monkeypatch.setattr(kbd, "_post", lambda ev: posted.append(ev))
    kbd.press_key("t", modifiers=["cmd", "shift"])
    assert [e["code"] for e in posted] == [17, 17]
    assert posted[0]["flags"] == (1 << 20) | (1 << 17)
    assert [e["down"] for e in posted] == [True, False]
