"""Accessibility (AX) tree reader — Aether's primary, cheap percept.

Reads the focused window of the frontmost app and returns a compact, flat list
of actionable UI elements with on-screen frames, so the agent can target real
elements (and AXPress them) instead of guessing pixels.

Robustness note: pyobjc exposes AX constants inconsistently across versions, so
we use raw attribute strings ("AXRole", ...) and defensive value extraction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any, Optional

try:
    from AppKit import NSWorkspace
    import ApplicationServices as AX
    _IMPORT_OK = True
except Exception:  # pragma: no cover - non-macOS / missing pyobjc
    _IMPORT_OK = False

# Attribute names (raw strings are stable across pyobjc versions)
A_ROLE = "AXRole"
A_SUBROLE = "AXSubrole"
A_TITLE = "AXTitle"
A_DESC = "AXDescription"
A_VALUE = "AXValue"
A_LABEL = "AXLabel"
A_ROLE_DESC = "AXRoleDescription"
A_CHILDREN = "AXChildren"
A_WINDOWS = "AXWindows"
A_FOCUSED_WINDOW = "AXFocusedWindow"
A_MAIN_WINDOW = "AXMainWindow"
A_POSITION = "AXPosition"
A_SIZE = "AXSize"
A_ENABLED = "AXEnabled"

# Roles we consider "actionable" enough to surface to the model.
ACTIONABLE_ROLES = {
    "AXButton", "AXMenuItem", "AXMenuButton", "AXCheckBox", "AXRadioButton",
    "AXTextField", "AXTextArea", "AXComboBox", "AXPopUpButton", "AXLink",
    "AXTab", "AXSlider", "AXCell", "AXRow", "AXSearchField", "AXToolbarButton",
    "AXStaticText", "AXImage", "AXDisclosureTriangle", "AXSegmentedControl",
}


@dataclass
class Element:
    idx: int
    role: str
    title: str
    value: str
    enabled: bool
    x: float
    y: float
    w: float
    h: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    def describe(self) -> str:
        label = self.title or self.value or ""
        label = (label[:60] + "…") if len(label) > 61 else label
        state = "" if self.enabled else " (disabled)"
        return (f"[{self.idx}] {self.role}{state} \"{label}\" "
                f"@({int(self.x)},{int(self.y)} {int(self.w)}x{int(self.h)})")


def available() -> bool:
    return _IMPORT_OK


def _copy(element, attr: str):
    try:
        err, val = AX.AXUIElementCopyAttributeValue(element, attr, None)
        if err == 0:  # kAXErrorSuccess
            return val
    except Exception:
        pass
    return None


def _to_str(v: Any) -> str:
    if v is None:
        return ""
    try:
        return str(v)
    except Exception:
        return ""


_POINT_RE = re.compile(r"x:([\-\d.]+)\s*y:([\-\d.]+)")
_SIZE_RE = re.compile(r"w:([\-\d.]+)\s*h:([\-\d.]+)")


def _value_pair(axvalue, kind: str) -> Optional[tuple[float, float]]:
    """Extract (x,y) / (w,h) from an AXValue, across pyobjc variants."""
    if axvalue is None:
        return None
    # 1) Try AXValueGetValue with whichever constant exists.
    getter = getattr(AX, "AXValueGetValue", None)
    if getter is not None:
        const_names = (("kAXValueCGPointType", "kAXValueTypeCGPoint")
                       if kind == "point" else
                       ("kAXValueCGSizeType", "kAXValueTypeCGSize"))
        for cn in const_names:
            const = getattr(AX, cn, None)
            if const is None:
                continue
            try:
                ok, out = getter(axvalue, const, None)
                if ok and out is not None:
                    if hasattr(out, "x"):
                        return (float(out.x), float(out.y))
                    if hasattr(out, "width"):
                        return (float(out.width), float(out.height))
            except Exception:
                continue
    # 2) Fall back to parsing the repr string.
    s = repr(axvalue)
    rx = _POINT_RE.search(s) if kind == "point" else _SIZE_RE.search(s)
    if rx:
        return (float(rx.group(1)), float(rx.group(2)))
    return None


def frontmost_app() -> dict:
    """Return {'name', 'pid', 'bundle'} for the frontmost application."""
    if not _IMPORT_OK:
        return {"name": "", "pid": -1, "bundle": ""}
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return {"name": "", "pid": -1, "bundle": ""}
    return {
        "name": _to_str(app.localizedName()),
        "pid": int(app.processIdentifier()),
        "bundle": _to_str(app.bundleIdentifier()),
    }


def list_running_apps() -> list[dict]:
    """Regular (Dock-visible) running apps: {'name', 'pid', 'bundle', 'active'}."""
    if not _IMPORT_OK:
        return []
    out = []
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        try:
            if int(app.activationPolicy()) != 0:  # NSApplicationActivationPolicyRegular
                continue
            out.append({
                "name": _to_str(app.localizedName()),
                "pid": int(app.processIdentifier()),
                "bundle": _to_str(app.bundleIdentifier()),
                "active": bool(app.isActive()),
            })
        except Exception:
            continue
    return out


def resolve_app(name: str) -> dict | None:
    """Find a running app by (case-insensitive) name or bundle id."""
    needle = (name or "").strip().lower()
    if not needle:
        return None
    for app in list_running_apps():
        if app["name"].lower() == needle or app["bundle"].lower() == needle:
            return app
    for app in list_running_apps():  # substring fallback
        if needle in app["name"].lower():
            return app
    return None


def _focused_window(app_el):
    for attr in (A_FOCUSED_WINDOW, A_MAIN_WINDOW):
        win = _copy(app_el, attr)
        if win is not None:
            return win
    windows = _copy(app_el, A_WINDOWS)
    if windows:
        try:
            return windows[0]
        except Exception:
            return None
    return None


def read_tree(max_elements: int = 250, max_depth: int = 14,
              capture_handles: bool = False, pid: int | None = None,
              handles_out: dict[int, Any] | None = None) -> list[Element]:
    """Walk an app's focused/main window AX tree -> flat list of Elements.

    pid=None reads the frontmost app (works for background apps too — AX
    reads ignore focus). handles_out, when given, receives idx->AXUIElement
    instead of registering into the global frontmost handle cache.
    """
    if not _IMPORT_OK:
        return []
    if pid is None:
        info = frontmost_app()
        pid = info["pid"]
    if pid < 0:
        return []

    app_el = AX.AXUIElementCreateApplication(pid)
    root = _focused_window(app_el) or app_el

    out: list[Element] = []
    handles: dict[int, Any] = {}
    counter = {"i": 0}

    def visit(el, depth: int):
        if len(out) >= max_elements or depth > max_depth:
            return
        role = _to_str(_copy(el, A_ROLE))
        if role:
            title = (_to_str(_copy(el, A_TITLE)) or _to_str(_copy(el, A_DESC))
                     or _to_str(_copy(el, A_LABEL)) or _to_str(_copy(el, A_ROLE_DESC)))
            value = _to_str(_copy(el, A_VALUE))
            enabled_v = _copy(el, A_ENABLED)
            enabled = True if enabled_v is None else bool(enabled_v)
            pos = _value_pair(_copy(el, A_POSITION), "point") or (0.0, 0.0)
            size = _value_pair(_copy(el, A_SIZE), "size") or (0.0, 0.0)

            interesting = (role in ACTIONABLE_ROLES) or bool(title) or bool(value)
            has_area = size[0] > 1 and size[1] > 1
            if interesting and has_area:
                idx = counter["i"]
                out.append(Element(
                    idx=idx, role=role, title=title, value=value,
                    enabled=enabled, x=pos[0], y=pos[1], w=size[0], h=size[1],
                ))
                if capture_handles:
                    handles[idx] = el
                counter["i"] += 1

        for child in (_copy(el, A_CHILDREN) or []):
            visit(child, depth + 1)

    try:
        visit(root, 0)
    except Exception:
        pass
    if capture_handles:
        if handles_out is not None:
            handles_out.update(handles)
        else:
            try:
                from ..effectors import ax_actions
                ax_actions.register_handles(handles)
            except Exception:
                pass
    return out


def screen_context(max_elements: int = 60, capture_handles: bool = True) -> dict:
    """Compact, model-friendly summary of what's on screen right now."""
    info = frontmost_app()
    els = read_tree(max_elements=max_elements, capture_handles=capture_handles)
    return {
        "frontmost_app": info["name"],
        "bundle_id": info["bundle"],
        "element_count": len(els),
        "elements": [asdict(e) for e in els],
        "rendered": "\n".join(e.describe() for e in els),
    }


def app_context(app_name: str, max_elements: int = 60,
                capture_handles: bool = True) -> dict:
    """AX summary of a named app — foreground or background (AX ignores focus).

    Captured handles are namespaced per app in ax_actions so background
    press/set-value can target them without disturbing the frontmost cache.
    """
    app = resolve_app(app_name)
    if app is None:
        return {"app": app_name, "error": f"app not running: {app_name}",
                "element_count": 0, "elements": [], "rendered": ""}
    handles: dict[int, Any] = {}
    els = read_tree(max_elements=max_elements, capture_handles=capture_handles,
                    pid=app["pid"], handles_out=handles)
    if capture_handles and handles:
        try:
            from ..effectors import ax_actions
            ax_actions.register_app_handles(app["name"], handles)
        except Exception:
            pass
    return {
        "app": app["name"],
        "bundle_id": app["bundle"],
        "pid": app["pid"],
        "active": app["active"],
        "element_count": len(els),
        "elements": [asdict(e) for e in els],
        "rendered": "\n".join(e.describe() for e in els),
    }


def list_windows() -> list[dict]:
    """Window titles per running app: [{'app', 'pid', 'windows': [...]}]."""
    if not _IMPORT_OK:
        return []
    out = []
    for app in list_running_apps():
        titles: list[str] = []
        try:
            app_el = AX.AXUIElementCreateApplication(app["pid"])
            for win in (_copy(app_el, A_WINDOWS) or []):
                t = _to_str(_copy(win, A_TITLE))
                if t:
                    titles.append(t[:80])
        except Exception:
            pass
        out.append({"app": app["name"], "pid": app["pid"], "windows": titles})
    return out


if __name__ == "__main__":
    if not _IMPORT_OK:
        raise SystemExit("pyobjc not available — run on macOS with deps installed.")
    ctx = screen_context()
    print(f"Frontmost: {ctx['frontmost_app']}  ({ctx['element_count']} elements)\n")
    print(ctx["rendered"])
