"""aether/screen_memory/gate.py: the single read path screen memory and hints share."""
from __future__ import annotations

from aether.screen_memory import gate
from aether.screen_memory.privacy import PrivacySettings, WindowState


def W(title="Notes — Groceries", bundle="com.apple.Notes", secure=False):  # noqa: ANN001, ANN201, N802
    return WindowState("Notes", bundle, title, secure, 42)


def test_decide_failure_short_circuits_before_any_check() -> None:
    calls = []
    state, reason = gate.readable_front(
        PrivacySettings(paused=True), ocr_fallback=True,
        probe=lambda: W(),
        read_text=lambda *a, **k: calls.append("read") or ("x", "ax"),  # noqa: ARG005
        check=lambda *a, **k: calls.append("check") or (False, "not a browser"))  # noqa: ARG005
    assert state is None and reason == "paused"
    assert not calls


def test_private_window_is_skipped_before_reading() -> None:
    calls = []
    state, reason = gate.readable_front(
        PrivacySettings(), ocr_fallback=True,
        probe=lambda: W(bundle="com.google.Chrome"),
        read_text=lambda *a, **k: calls.append("read") or ("x", "ax"),  # noqa: ARG005
        check=lambda *a, **k: (True, "mode"))
    assert state is None and reason == "private window"
    assert not calls


def test_chromium_check_failure_reads_as_not_confirmed() -> None:
    state, reason = gate.readable_front(
        PrivacySettings(), ocr_fallback=True, probe=lambda: W(bundle="com.google.Chrome"),
        read_text=lambda *a, **k: ("x", "ax"),  # noqa: ARG005
        check=lambda *a, **k: (None, "mode unknown"))
    assert state is None and reason == "can't confirm the window isn't private"


def test_not_allowed_browser_is_skipped_by_decide_before_any_check() -> None:
    calls = []
    state, reason = gate.readable_front(
        PrivacySettings(), ocr_fallback=True, probe=lambda: W(bundle="com.apple.Safari"),
        read_text=lambda *a, **k: calls.append("read") or ("x", "ax"),  # noqa: ARG005
        check=lambda *a, **k: calls.append("check") or (None, "not allowed"))  # noqa: ARG005
    assert state is None and reason == "browser not allowed"
    assert not calls          # the cheap decide() already ruled it out


def test_check_not_allowed_maps_to_the_same_clear_reason() -> None:
    """Exercises the gate's own mapping in isolation, past decide()'s (redundant) filter."""
    state, reason = gate.readable_front(
        PrivacySettings(allowed_browsers=["com.apple.Safari"]), ocr_fallback=True,
        probe=lambda: W(bundle="com.apple.Safari"),
        read_text=lambda *a, **k: ("x", "ax"),  # noqa: ARG005
        check=lambda *a, **k: (None, "not allowed"))  # noqa: ARG005
    assert state is None and reason == "browser not allowed"


def test_secure_focus_at_probe_time_is_a_password_field() -> None:
    state, reason = gate.readable_front(
        PrivacySettings(), ocr_fallback=True, probe=lambda: W(secure=True),
        read_text=lambda *a, **k: ("x", "ax"),  # noqa: ARG005
        check=lambda *a, **k: (False, "not a browser"))
    assert state is None and reason == "password field"


def test_window_changed_between_probe_and_read_is_dropped() -> None:
    calls = {"n": 0}

    def probe():  # noqa: ANN202
        calls["n"] += 1
        return W(title="Groceries") if calls["n"] == 1 else W(title="Private Browsing")

    state, reason = gate.readable_front(
        PrivacySettings(), ocr_fallback=True, probe=probe,
        read_text=lambda *a, **k: ("some text", "ax"),  # noqa: ARG005
        check=lambda *a, **k: (False, "not a browser"))
    assert state is None and reason == "window changed while reading"
    assert calls["n"] == 2


def test_window_changed_bundle_is_dropped() -> None:
    calls = {"n": 0}

    def probe():  # noqa: ANN202
        calls["n"] += 1
        return W(bundle="com.apple.Notes") if calls["n"] == 1 else W(bundle="com.apple.Mail")

    state, reason = gate.readable_front(
        PrivacySettings(), ocr_fallback=True, probe=probe,
        read_text=lambda *a, **k: ("some text", "ax"),  # noqa: ARG005
        check=lambda *a, **k: (False, "not a browser"))
    assert state is None and reason == "window changed while reading"


def test_password_field_focused_between_probe_and_read_is_dropped() -> None:
    calls = {"n": 0}

    def probe():  # noqa: ANN202
        calls["n"] += 1
        return W() if calls["n"] == 1 else W(secure=True)

    state, reason = gate.readable_front(
        PrivacySettings(), ocr_fallback=True, probe=probe,
        read_text=lambda *a, **k: ("some text", "ax"),  # noqa: ARG005
        check=lambda *a, **k: (False, "not a browser"))
    assert state is None and reason == "window changed while reading"


def test_chromium_re_checked_after_reading() -> None:
    """A window that switches into private mode mid-read is caught on the re-check."""
    checks = []

    def check(state, allowed):  # noqa: ANN001, ANN202, ARG001
        checks.append(state)
        return (False, "mode") if len(checks) == 1 else (True, "mode")

    state, reason = gate.readable_front(
        PrivacySettings(), ocr_fallback=True, probe=lambda: W(bundle="com.google.Chrome"),
        read_text=lambda *a, **k: ("some text", "ax"),  # noqa: ARG005
        check=check)
    assert state is None and reason == "window changed while reading"
    assert len(checks) == 2


def test_chromium_second_check_unconfirmed_is_also_dropped() -> None:
    checks = []

    def check(state, allowed):  # noqa: ANN001, ANN202, ARG001
        checks.append(state)
        return (False, "mode") if len(checks) == 1 else (None, "mode unknown")

    state, reason = gate.readable_front(
        PrivacySettings(), ocr_fallback=True, probe=lambda: W(bundle="com.google.Chrome"),
        read_text=lambda *a, **k: ("some text", "ax"),  # noqa: ARG005
        check=check)
    assert state is None and reason == "window changed while reading"


def test_ocr_fallback_disabled_for_a_known_browser() -> None:
    seen = {}

    def read_text(state, *, ocr_fallback):  # noqa: ANN001, ANN202
        seen["ocr_fallback"] = ocr_fallback
        return "page text", "ax"

    state, text = gate.readable_front(
        PrivacySettings(), ocr_fallback=True, probe=lambda: W(bundle="com.google.Chrome"),
        read_text=read_text, check=lambda *a, **k: (False, "mode"))  # noqa: ARG005
    assert state is not None and text == "page text"
    assert seen["ocr_fallback"] is False


def test_ocr_fallback_kept_for_a_non_browser() -> None:
    seen = {}

    def read_text(state, *, ocr_fallback):  # noqa: ANN001, ANN202
        seen["ocr_fallback"] = ocr_fallback
        return "note text", "ax"

    state, text = gate.readable_front(
        PrivacySettings(), ocr_fallback=True, probe=lambda: W(),
        read_text=read_text, check=lambda *a, **k: (False, "not a browser"))  # noqa: ARG005
    assert state is not None and text == "note text"
    assert seen["ocr_fallback"] is True


def test_successful_read_returns_state_and_text() -> None:
    state, text = gate.readable_front(
        PrivacySettings(), ocr_fallback=False, probe=lambda: W(),
        read_text=lambda *a, **k: ("hello", "ax"),  # noqa: ARG005
        check=lambda *a, **k: (False, "not a browser"))  # noqa: ARG005
    assert isinstance(state, WindowState) and text == "hello"


def test_allowed_browsers_reach_the_check(monkeypatch) -> None:  # noqa: ANN001
    seen = {}

    def check(state, allowed):  # noqa: ANN001, ANN202
        seen["allowed"] = allowed
        return False, "title"

    gate.readable_front(
        PrivacySettings(allowed_browsers=["com.apple.Safari"]), ocr_fallback=False,
        probe=lambda: W(bundle="com.apple.Safari"),
        read_text=lambda *a, **k: ("hello", "ax"),  # noqa: ARG005
        check=check)
    assert seen["allowed"] == {"com.apple.Safari"}


def test_messaging_timeout_is_set_once(monkeypatch) -> None:  # noqa: ANN001
    calls = []
    monkeypatch.setattr(gate.ax, "set_messaging_timeout", lambda s: calls.append(s))
    monkeypatch.setattr(gate, "_timeout_set", False)
    for _ in range(3):
        gate.readable_front(
            PrivacySettings(), ocr_fallback=False, probe=lambda: W(),
            read_text=lambda *a, **k: ("x", "ax"),  # noqa: ARG005
            check=lambda *a, **k: (False, "not a browser"))  # noqa: ARG005
    assert calls == [1.0]
