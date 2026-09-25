"""The single gate screen memory and hints read the front window through.

Combines the cheap decision (``privacy.decide``), the browser-family
private-window check (``browsers.check_private``) and a re-probe right after
reading, so a switch to a private window between the first check and the read
can't leak its text under the old window's title or app.
"""
from __future__ import annotations

from collections.abc import Callable

from ..perception import accessibility as ax
from .browsers import check_private, family
from .privacy import Decision, PrivacySettings, WindowState, decide
from .recorder import probe_front, read_window_text

_timeout_set = False


def _set_messaging_timeout_once() -> None:
    """AX calls can otherwise block indefinitely on a stuck or busy app."""
    global _timeout_set
    if not _timeout_set:
        _timeout_set = True
        ax.set_messaging_timeout(1.0)


def readable_front(
    privacy: PrivacySettings, *, ocr_fallback: bool,
    probe: Callable[[], WindowState] = probe_front,
    read_text: Callable[..., tuple[str, str]] = read_window_text,
    check: Callable[..., tuple[bool | None, str]] = check_private,
) -> tuple[WindowState, str] | tuple[None, str]:
    """The front window and its text, or ``(None, reason)`` when it may not be read.

    Order: the cheap privacy decision; the browser private-window check (skip
    on "yes" or "can't tell"); the actual read; a re-probe, since the window
    that was in front when we decided to read it is not necessarily the one
    whose text just came back.
    """
    _set_messaging_timeout_once()
    state = probe()
    decision: Decision = decide(state, privacy)
    if not decision.allowed:
        return None, decision.reason
    allowed = set(privacy.allowed_browsers)
    private, how = check(state, allowed)
    if private:
        return None, "private window"
    if private is None:
        return None, ("browser not allowed" if how == "not allowed"
                      else "can't confirm the window isn't private")
    fam = family(state.bundle_id)
    # A browser we already had to ask AppleScript about gets no OCR fallback:
    # OCR would read the page itself, defeating the point of asking first.
    text, _source = read_text(state, ocr_fallback=ocr_fallback and fam is None)
    again = probe()
    if (again.bundle_id != state.bundle_id or again.window_title != state.window_title
            or again.pid != state.pid or again.window_id != state.window_id
            or again.secure_focus):
        return None, "window changed while reading"
    if fam is not None:
        # Every browser family is checked again: Chrome-family asks the browser,
        # the others re-apply their title rules to the window now in front.
        private_again, _how = check(again, allowed)
        if private_again is not False:
            return None, "window changed while reading"
    return state, text
