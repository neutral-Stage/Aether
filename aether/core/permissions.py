"""macOS TCC permission checks for the Phase 0 primitives.

Phase 0 needs:
  - Accessibility  (read the AX tree, synthesize input)
  - Screen Recording (capture frames)
  - Microphone (optional, voice input)

Grant these in System Settings -> Privacy & Security. The terminal/app you run
Aether from is the process that must be granted (e.g. Terminal.app or iTerm).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PermissionStatus:
    accessibility: bool
    screen_recording: bool | None  # None = couldn't determine
    note: str = ""


def check_accessibility(prompt: bool = False) -> bool:
    """True if this process is trusted for the Accessibility API."""
    try:
        from ApplicationServices import (
            AXIsProcessTrusted,
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
    except Exception:
        return False

    if prompt:
        try:
            options = {kAXTrustedCheckOptionPrompt: True}
            return bool(AXIsProcessTrustedWithOptions(options))
        except Exception:
            pass
    try:
        return bool(AXIsProcessTrusted())
    except Exception:
        return False


def check_screen_recording(request: bool = False) -> bool | None:
    """True/False if determinable, else None.

    Uses CGPreflightScreenCaptureAccess (macOS 10.15+). If `request` is set and
    access is missing, triggers the system prompt once.
    """
    try:
        import Quartz
    except Exception:
        return None

    preflight = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
    requestfn = getattr(Quartz, "CGRequestScreenCaptureAccess", None)
    if preflight is None:
        return None
    try:
        ok = bool(preflight())
        if not ok and request and requestfn is not None:
            requestfn()  # shows the system dialog; takes effect after restart
            ok = bool(preflight())
        return ok
    except Exception:
        return None


def status(prompt: bool = False) -> PermissionStatus:
    ax = check_accessibility(prompt=prompt)
    sr = check_screen_recording(request=False)
    note = ""
    if not ax:
        note = ("Accessibility is OFF. Grant it in System Settings -> Privacy & "
                "Security -> Accessibility for your terminal/app, then restart it.")
    return PermissionStatus(accessibility=ax, screen_recording=sr, note=note)


def report(prompt: bool = True) -> PermissionStatus:
    """Print a human-readable permission report and return the status."""
    st = status(prompt=prompt)

    def mark(v: bool | None) -> str:
        return {True: "GRANTED", False: "MISSING", None: "UNKNOWN"}[v]

    print("Aether — permission check")
    print(f"  Accessibility   : {mark(st.accessibility)}")
    print(f"  Screen Recording: {mark(st.screen_recording)}")
    if st.note:
        print(f"\n  ! {st.note}")
    return st


if __name__ == "__main__":
    report(prompt=True)
