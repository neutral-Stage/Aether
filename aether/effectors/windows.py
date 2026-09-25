"""Window management: bring a window forward, move/resize it.

Accessibility first (works without stealing more focus than needed), System
Events AppleScript as the fallback.
"""
from __future__ import annotations

from ..perception import accessibility as ax

try:
    import ApplicationServices as AX
    _OK = ax.available()
except Exception:  # pragma: no cover
    _OK = False


def _as_quote(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _windows(pid: int) -> list:
    app_el = AX.AXUIElementCreateApplication(pid)
    return list(ax._copy(app_el, "AXWindows") or [])  # noqa: SLF001


def _pick(windows: list, title_contains: str | None):  # noqa: ANN202
    if not windows:
        return None
    if not title_contains:
        return windows[0]
    want = title_contains.casefold()
    for w in windows:
        if want in ax._to_str(ax._copy(w, "AXTitle")).casefold():  # noqa: SLF001
            return w
    return None


def focus_window(app: str, title_contains: str | None = None) -> str:
    """Activate the app and raise the matching window."""
    info = ax.resolve_app(app)
    if info is None:
        from .apps import open_app

        return open_app(app)
    name = info["name"]
    if _OK:
        try:
            from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication

            running = NSRunningApplication.runningApplicationWithProcessIdentifier_(info["pid"])
            if running is not None:
                running.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            win = _pick(_windows(info["pid"]), title_contains)
            if title_contains and win is None:
                return f"ERROR: {name} has no window containing '{title_contains}'."
            if win is not None:
                AX.AXUIElementPerformAction(win, "AXRaise")
                AX.AXUIElementSetAttributeValue(win, "AXMain", True)
                title = ax._to_str(ax._copy(win, "AXTitle"))  # noqa: SLF001
                return f"Focused {name}" + (f" — '{title}'" if title else "") + "."
            return f"Activated {name} (no windows)."
        except Exception:  # noqa: BLE001 — fall through to AppleScript
            pass
    from .applescript import run_applescript

    script = f"tell application {_as_quote(name)} to activate"
    if title_contains:
        script += (f'\ntell application "System Events" to tell process {_as_quote(name)} to '
                   f"perform action \"AXRaise\" of (first window whose name contains "
                   f"{_as_quote(title_contains)})")
    res = run_applescript(script)
    return (f"Focused {name}." if res.returncode == 0
            else f"ERROR: could not focus {name}: {res.summary(200)}")


def set_window_frame(app: str, x: float | None = None, y: float | None = None,
                     width: float | None = None, height: float | None = None,
                     title_contains: str | None = None) -> str:
    """Move and/or resize a window (global top-left points)."""
    info = ax.resolve_app(app)
    if info is None:
        return f"ERROR: app not running: {app}"
    name = info["name"]
    if _OK:
        try:
            import Quartz

            win = _pick(_windows(info["pid"]), title_contains)
            if win is None:
                return f"ERROR: no matching window in {name}."
            if x is not None and y is not None:
                pos = AX.AXValueCreate(AX.kAXValueCGPointType, Quartz.CGPointMake(x, y))
                AX.AXUIElementSetAttributeValue(win, "AXPosition", pos)
            if width is not None and height is not None:
                size = AX.AXValueCreate(AX.kAXValueCGSizeType, Quartz.CGSizeMake(width, height))
                AX.AXUIElementSetAttributeValue(win, "AXSize", size)
            return f"Set {name} window frame."
        except Exception:  # noqa: BLE001 — fall back to AppleScript
            pass
    from .applescript import run_applescript

    target = (f"(first window whose name contains {_as_quote(title_contains)})"
              if title_contains else "front window")
    lines = [f'tell application "System Events" to tell process {_as_quote(name)}']
    if x is not None and y is not None:
        lines.append(f"  set position of {target} to {{{int(x)}, {int(y)}}}")
    if width is not None and height is not None:
        lines.append(f"  set size of {target} to {{{int(width)}, {int(height)}}}")
    lines.append("end tell")
    res = run_applescript("\n".join(lines))
    return (f"Set {name} window frame." if res.returncode == 0
            else f"ERROR: could not move/resize {name}: {res.summary(200)}")
