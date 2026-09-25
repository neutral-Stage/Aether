"""aether/screen_memory/browsers.py: family table and the private-window check."""
from __future__ import annotations

import pytest

from aether.effectors.applescript import AppleScriptResult
from aether.screen_memory import browsers
from aether.screen_memory.privacy import WindowState


def W(bundle, title="Some Page"):  # noqa: ANN001, ANN201, N802
    return WindowState("App", bundle, title, False, 1)


@pytest.mark.parametrize(("bundle", "fam"), [
    ("com.google.Chrome", "chromium"),
    ("com.brave.Browser", "chromium"),
    ("com.microsoft.edgemac", "chromium"),
    ("com.vivaldi.Vivaldi", "chromium"),
    ("org.mozilla.firefox", "firefox"),
    ("app.zen-browser.zen", "firefox"),
    ("com.apple.Safari", "safari"),
    ("com.apple.SafariTechnologyPreview", "safari"),
    ("company.thebrowser.Browser", "unknown"),
    ("com.operasoftware.Opera", "unknown"),
    ("com.kagi.kagimacOS", "unknown"),
    ("com.duckduckgo.macos.browser", "unknown"),
    ("com.apple.Notes", None),
    (None, None),
])
def test_family_table(bundle, fam) -> None:  # noqa: ANN001
    assert browsers.family(bundle) == fam


def test_not_a_browser_is_never_private() -> None:
    assert browsers.check_private(W("com.apple.Notes"), set()) == (False, "not a browser")


@pytest.mark.parametrize(("returncode", "stdout", "stderr", "want"), [
    (0, "normal", "", (False, "mode")),
    (0, "normal\n", "", (False, "mode")),
    (0, "incognito", "", (True, "mode")),
    (0, "InPrivate", "", (True, "mode")),
    (1, "", "error", (None, "mode unknown")),
    (124, "", "timeout", (None, "mode unknown")),
    (0, "", "", (None, "mode unknown")),          # empty stdout: can't tell
])
def test_chromium_check_private(returncode, stdout, stderr, want) -> None:  # noqa: ANN001
    seen = {}

    def fake_run(source, args, timeout=10):  # noqa: ANN001, ANN202
        seen["source"] = source
        seen["args"] = args
        seen["timeout"] = timeout
        return AppleScriptResult(returncode, stdout, stderr)

    state = W("com.google.Chrome")
    assert browsers.check_private(state, set(), run=fake_run) == want
    assert seen["args"] == ["com.google.Chrome"]
    assert "on run argv" in seen["source"]
    assert seen["timeout"] <= 5


def test_chromium_check_ignores_allowed_set() -> None:
    """allowed only matters for safari/unknown; a failed chromium check is always None."""
    fake_run = lambda source, args, timeout=10: AppleScriptResult(1, "", "boom")  # noqa: E731, ARG005
    state = W("com.google.Chrome")
    assert browsers.check_private(state, {"com.google.Chrome"}, run=fake_run) == \
        (None, "mode unknown")


@pytest.mark.parametrize(("title", "want"), [
    ("Mozilla Firefox — Private Browsing", (True, "title")),
    ("My Bank — Mozilla Firefox", (False, "title")),
])
def test_firefox_uses_title(title, want) -> None:  # noqa: ANN001
    assert browsers.check_private(W("org.mozilla.firefox", title), set()) == want


def test_safari_not_allowed_is_unconfirmed() -> None:
    assert browsers.check_private(W("com.apple.Safari"), set()) == (None, "not allowed")
    assert browsers.check_private(W("com.apple.Safari"), {"com.google.Chrome"}) == \
        (None, "not allowed")


@pytest.mark.parametrize(("title", "want"), [
    ("Private Browsing", (True, "title")),
    ("My Bank — Safari", (False, "title")),
])
def test_safari_allowed_uses_title(title, want) -> None:  # noqa: ANN001
    assert browsers.check_private(W("com.apple.Safari", title), {"com.apple.Safari"}) == want


def test_unknown_family_allowed_uses_title() -> None:
    state = W("company.thebrowser.Browser", "Incognito")
    assert browsers.check_private(state, set()) == (None, "not allowed")
    assert browsers.check_private(state, {"company.thebrowser.Browser"}) == (True, "title")
