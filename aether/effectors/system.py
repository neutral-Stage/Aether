"""Open files/URLs, post notifications, read the user's selected text."""
from __future__ import annotations

import os
import subprocess
import time

from ..perception import accessibility as ax

try:
    import ApplicationServices as AX
    _OK = ax.available()
except Exception:  # pragma: no cover
    _OK = False


def _as_quote(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def open_path(path: str, app: str | None = None) -> str:
    """Open a file or folder with its default app (or a named one)."""
    p = os.path.expanduser(str(path or ""))
    if not p:
        return "ERROR: path is required."
    if not os.path.exists(p):
        return f"ERROR: no such file or folder: {p}"
    cmd = ["open", p] if not app else ["open", "-a", str(app), p]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)  # noqa: S603
    if res.returncode != 0:
        return f"ERROR: could not open {p}: {res.stderr.strip()}"
    time.sleep(0.5)
    return f"Opened {p}" + (f" in {app}" if app else "") + "."


def open_url(url: str) -> str:
    """Open a URL in the default browser (or its registered handler)."""
    u = str(url or "").strip()
    if not u:
        return "ERROR: url is required."
    res = subprocess.run(["open", u], capture_output=True, text=True, timeout=15)  # noqa: S603
    if res.returncode != 0:
        return f"ERROR: could not open {u}: {res.stderr.strip()}"
    time.sleep(0.5)
    return f"Opened {u}."


def notification_script(title: str, body: str) -> str:
    return (f"display notification {_as_quote(body)} "
            f"with title {_as_quote(title or 'Aether')}")


def quit_app_script(name: str) -> str:
    return f"tell application {_as_quote(name)} to quit"


def quit_app(name: str) -> str:
    """Ask an app to quit normally (it still asks to save unsaved work)."""
    res = subprocess.run(["osascript", "-e", quit_app_script(name)],  # noqa: S603
                         capture_output=True, text=True, timeout=20)
    return f"Quit {name}." if res.returncode == 0 else f"ERROR: {res.stderr.strip()}"


def volume_script(level: int | None = None, change: int | None = None,
                  muted: bool | None = None) -> str:
    """AppleScript for the output volume (0–100), a relative change, or mute."""
    if muted is not None:
        return f"set volume output muted {'true' if muted else 'false'}"
    if change is not None:
        return ("set v to output volume of (get volume settings)\n"
                f"set volume output volume (v + ({int(change)}))\n"
                "set volume output muted false")
    lvl = max(0, min(100, int(level if level is not None else 50)))
    return f"set volume output volume {lvl}\nset volume output muted false"


def set_volume(level: int | None = None, change: int | None = None,
               muted: bool | None = None) -> str:
    res = subprocess.run(  # noqa: S603
        ["osascript", "-e", volume_script(level, change, muted),
         "-e", "output volume of (get volume settings)"],
        capture_output=True, text=True, timeout=10)
    if res.returncode != 0:
        return f"ERROR: {res.stderr.strip()}"
    if muted:
        return "Sound muted."
    return f"Volume is {res.stdout.strip() or '?'}%."


def notify(title: str, body: str) -> str:
    res = subprocess.run(["osascript", "-e", notification_script(title, body)],  # noqa: S603
                         capture_output=True, text=True, timeout=10)
    return "Notification shown." if res.returncode == 0 else f"ERROR: {res.stderr.strip()}"


def get_selected_text(copy_fallback: bool = True) -> str:
    """The text selected in the focused app (AX), else via ⌘C with restore."""
    if _OK:
        try:
            system = AX.AXUIElementCreateSystemWide()
            focused = ax._copy(system, "AXFocusedUIElement")  # noqa: SLF001
            if focused is not None:
                role = ax._to_str(ax._copy(focused, "AXRole"))  # noqa: SLF001
                subrole = ax._to_str(ax._copy(focused, "AXSubrole"))  # noqa: SLF001
                if "Secure" in role or "Secure" in subrole:
                    return ""  # never read password fields
                sel = ax._to_str(ax._copy(focused, "AXSelectedText"))  # noqa: SLF001
                if sel:
                    return sel
        except Exception:  # noqa: BLE001
            pass
    if not copy_fallback:
        return ""
    from . import clipboard
    from . import input as kbd

    saved = clipboard.snapshot()
    before = clipboard.change_count()
    kbd.press_key("c", modifiers=["cmd"])
    time.sleep(0.25)
    text = clipboard.get_text() if clipboard.change_count() != before else ""
    clipboard.restore(saved)
    return text
