"""Which family a browser belongs to, and whether its front window is private.

Chrome-family browsers expose a private-mode flag AppleScript can just ask for,
so those are checked directly (`check_private`) and skipped whenever the check
fails — fail closed. Firefox has no such API; its private windows are still
recognised by their title, as before. Safari and browsers whose private
windows can't be detected at all (Arc, Opera, Orion, DuckDuckGo) are skipped
unless the owner explicitly allows the bundle (`PrivacySettings.allowed_browsers`).
"""
from __future__ import annotations

from collections.abc import Callable

from ..effectors.applescript import AppleScriptResult, run_applescript_args
from .privacy import _PRIVATE_WINDOW, WindowState

# Ask directly: `tell application id "<bundle>" to get mode of front window`
# returns "normal" or "incognito" (Chrome/Brave/Vivaldi) or "InPrivate" (Edge).
CHROMIUM = frozenset({
    "com.google.Chrome", "com.google.Chrome.canary", "com.google.Chrome.beta",
    "com.google.Chrome.dev", "org.chromium.Chromium", "com.brave.Browser",
    "com.brave.Browser.beta", "com.brave.Browser.nightly", "com.microsoft.edgemac",
    "com.microsoft.edgemac.Beta", "com.microsoft.edgemac.Dev", "com.microsoft.edgemac.Canary",
    "com.vivaldi.Vivaldi",
})
# No API for this; recognised by title only, as screen memory always has.
FIREFOX = frozenset({
    "org.mozilla.firefox", "org.mozilla.firefoxdeveloperedition", "org.mozilla.nightly",
    "org.mozilla.librewolf", "app.zen-browser.zen",
})
# Apple gives no way to ask Safari whether a window is private.
SAFARI = frozenset({"com.apple.Safari", "com.apple.SafariTechnologyPreview"})
# Chromium-based or otherwise, but without a working mode check.
UNKNOWN = frozenset({
    "company.thebrowser.Browser", "com.operasoftware.Opera", "com.kagi.kagimacOS",
    "com.duckduckgo.macos.browser",
})

_FAMILIES: dict[str, str] = {
    **dict.fromkeys(CHROMIUM, "chromium"),
    **dict.fromkeys(FIREFOX, "firefox"),
    **dict.fromkeys(SAFARI, "safari"),
    **dict.fromkeys(UNKNOWN, "unknown"),
}

# `on run argv` so the bundle id is never spliced into the script text.
_MODE_SCRIPT = (
    "on run argv\n"
    "    tell application id (item 1 of argv) to get mode of front window\n"
    "end run"
)
_MODE_TIMEOUT_S = 3


def family(bundle_id: str | None) -> str | None:
    """"chromium" | "firefox" | "safari" | "unknown" | None (not a browser we know)."""
    return _FAMILIES.get(bundle_id or "")


def check_private(state: WindowState, allowed: set[str], *,
                  run: Callable[..., AppleScriptResult] = run_applescript_args,
                  ) -> tuple[bool | None, str]:
    """(private?, how) for the window ``state`` describes.

    ``private`` is ``None`` when it could not be confirmed either way — the
    caller must treat that as "don't record" (fail closed). ``allowed`` only
    changes anything for Safari and unknown-family browsers: Chrome-family
    browsers are always checked directly, and a failed check is always
    ``None`` regardless of ``allowed``.
    """
    fam = family(state.bundle_id)
    if fam is None:
        return False, "not a browser"
    if fam == "chromium":
        result: AppleScriptResult = run(_MODE_SCRIPT, [str(state.bundle_id)],
                                        timeout=_MODE_TIMEOUT_S)
        mode = (result.stdout or "").strip()
        if result.returncode != 0 or not mode:
            return None, "mode unknown"
        return (mode != "normal"), "mode"
    if fam == "firefox":
        return bool(_PRIVATE_WINDOW.search(state.window_title or "")), "title"
    # safari / unknown: no reliable check exists, so only look at the title,
    # and only for a bundle the owner explicitly allowed.
    if state.bundle_id not in (allowed or set()):
        return None, "not allowed"
    return bool(_PRIVATE_WINDOW.search(state.window_title or "")), "title"
