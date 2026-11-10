"""Application control: launch, activate, enumerate."""
from __future__ import annotations

import subprocess

try:
    from AppKit import NSWorkspace
    _OK = True
except Exception:  # pragma: no cover
    _OK = False


def open_app(name: str) -> str:
    """Launch or focus an app by name (e.g. 'Safari', 'Notes')."""
    try:
        subprocess.run(["open", "-a", name], check=True,
                       capture_output=True, text=True, timeout=15)
        return f"Opened {name}"
    except subprocess.CalledProcessError as e:
        return f"Failed to open {name}: {e.stderr.strip() or e}"
    except Exception as e:  # noqa: BLE001
        return f"Failed to open {name}: {e}"


def activate_app(name: str) -> str:
    """Bring an already-running app to the front via AppleScript."""
    script = f'tell application "{name}" to activate'
    try:
        subprocess.run(["osascript", "-e", script], check=True,
                       capture_output=True, text=True, timeout=10)
        return f"Activated {name}"
    except Exception as e:  # noqa: BLE001
        return f"Failed to activate {name}: {e}"


def frontmost() -> str:
    if not _OK:
        return ""
    try:
        from ..perception import accessibility as ax
        name = ax.frontmost_app().get("name", "")
        if name:
            return name
    except Exception:
        pass
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return app.localizedName() if app else ""


def list_running() -> list[str]:
    if not _OK:
        return []
    try:
        from ..perception import accessibility as ax
        live = [a["name"] for a in ax.list_running_apps() if a.get("name")]
        if live:
            return sorted(set(live))
    except Exception:
        pass
    apps = NSWorkspace.sharedWorkspace().runningApplications()
    names = []
    for a in apps:
        # activationPolicy 0 == regular (has a UI)
        try:
            if a.activationPolicy() == 0 and a.localizedName():
                names.append(str(a.localizedName()))
        except Exception:
            continue
    return sorted(set(names))


if __name__ == "__main__":
    print("Frontmost:", frontmost())
    print("Running UI apps:", ", ".join(list_running()))
