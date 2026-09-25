"""What screen memory may record. Every rule fails closed: when in doubt, skip."""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

AETHER_BUNDLE = "dev.aether.macos"

# Never recorded, whatever the settings say.
PASSWORD_MANAGERS = frozenset({
    "com.1password.1password", "com.agilebits.onepassword7", "com.agilebits.onepassword-osx",
    "com.bitwarden.desktop", "com.apple.keychainaccess", "com.apple.Passwords",
    "org.keepassxc.keepassxc", "com.dashlane.dashlanephonefinal", "com.lastpass.LastPass",
    "com.enpass.Enpass-Desktop", "com.nordpass.macos.NordPass",
})
_PRIVATE_WINDOW = re.compile(r"private browsing|incognito|inprivate|private window|\(private\)",
                             re.I)
_SENSITIVE_TITLE = re.compile(r"\b(?:bank(?:ing)?|password|passcode|one-time code|2fa|"
                              r"verification code|credit card|sign[ -]?in|log[ -]?in)\b", re.I)


@dataclass
class WindowState:
    """What is in front. ``None`` means it could not be read (and is not recorded)."""
    app: str | None
    bundle_id: str | None
    window_title: str | None
    secure_focus: bool = False
    pid: int = -1


@dataclass
class PrivacySettings:
    paused: bool = False
    exclude_bundle_ids: list[str] = field(default_factory=list)
    exclude_window_globs: list[str] = field(default_factory=list)
    # When set, ONLY these apps are recorded.
    only_bundle_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


def decide(state: WindowState, settings: PrivacySettings) -> Decision:
    if settings.paused:
        return Decision(False, "paused")
    if not state.app or not state.bundle_id:
        return Decision(False, "unknown app")
    if state.window_title is None or not state.window_title.strip():
        return Decision(False, "unknown window")
    if state.secure_focus:
        return Decision(False, "password field")
    bundle = state.bundle_id
    if bundle == AETHER_BUNDLE:
        return Decision(False, "Aether itself")
    if bundle in PASSWORD_MANAGERS:
        return Decision(False, "password manager")
    if bundle in settings.exclude_bundle_ids:
        return Decision(False, "excluded app")
    if settings.only_bundle_ids and bundle not in settings.only_bundle_ids:
        return Decision(False, "not in the allowed apps")
    title = state.window_title
    if _PRIVATE_WINDOW.search(title):
        return Decision(False, "private window")
    if _SENSITIVE_TITLE.search(title):
        return Decision(False, "sensitive page")
    lowered = title.lower()
    for pattern in settings.exclude_window_globs:
        if fnmatch.fnmatch(lowered, pattern.lower()):
            return Decision(False, "excluded window")
    return Decision(True, "ok")
