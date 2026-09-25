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
    identifier: str = ""   # AXIdentifier (developer id; stable across locales)
    help: str = ""         # AXHelp (tooltip text)

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


def _frame_of(handle: Any) -> tuple[float, float, float, float] | None:
    pos = _value_pair(_copy(handle, A_POSITION), "point")
    size = _value_pair(_copy(handle, A_SIZE), "size")
    if not pos or not size:
        return None
    return (pos[0], pos[1], size[0], size[1])


def _label_of_handle(handle: Any) -> str:
    return (_to_str(_copy(handle, A_TITLE)) or _to_str(_copy(handle, A_DESC))
            or _to_str(_copy(handle, A_LABEL)) or "")


def _contains(frame: tuple[float, float, float, float] | None, x: float, y: float) -> bool:
    return bool(frame) and frame[0] <= x <= frame[0] + frame[2] and frame[1] <= y <= frame[1] + frame[3]


def drill_to_smallest(handle: Any, x: float, y: float, *, children_of=None, frame_of=None,
                      max_depth: int = 8) -> Any:
    """Descend to the smallest child whose frame holds the point.

    Hit-testing often returns a large container (web areas, Electron and
    custom views); its children usually carry the real control.
    """
    children_of = children_of or (lambda h: list(_copy(h, A_CHILDREN) or []))
    frame_of = frame_of or _frame_of
    current = handle
    for _ in range(max_depth):
        best, best_area = None, None
        for child in children_of(current) or []:
            f = frame_of(child)
            if not _contains(f, x, y) or f[2] < 2 or f[3] < 2:
                continue
            area = f[2] * f[3]
            if best_area is None or area < best_area:
                best, best_area = child, area
        if best is None:
            return current
        current = best
    return current


def element_at(x: float, y: float) -> dict | None:
    """What is under a screen point: the element, where it lives, and context.

    Returns {element, ancestry, window, app, url} or None. ``url`` is set when
    the element is inside a web page (AXURL of the page or document). Secure
    text fields report no value.
    """
    if not _IMPORT_OK:
        return None
    try:
        system = AX.AXUIElementCreateSystemWide()
        err, hit = AX.AXUIElementCopyElementAtPosition(system, float(x), float(y), None)
    except Exception:  # noqa: BLE001
        return None
    if err != 0 or hit is None:
        return None
    el = drill_to_smallest(hit, x, y)
    role = _to_str(_copy(el, A_ROLE))
    subrole = _to_str(_copy(el, A_SUBROLE))
    frame = _frame_of(el) or (x, y, 0.0, 0.0)
    secure = "Secure" in role or "Secure" in subrole
    value = "" if secure else _to_str(_copy(el, A_VALUE))[:500]
    enabled = _copy(el, A_ENABLED)
    info = Element(0, role, _label_of_handle(el) or _to_str(_copy(el, A_ROLE_DESC)), value,
                   bool(enabled) if enabled is not None else True, *frame,
                   identifier=_to_str(_copy(el, "AXIdentifier")),
                   help=_to_str(_copy(el, "AXHelp")))
    ancestry: list[str] = []
    window = app = url = ""
    node = el
    for _ in range(24):
        if not url:
            raw = _copy(node, "AXURL") or _copy(node, "AXDocument")
            url = _to_str(raw) if raw is not None else ""
        parent = _copy(node, "AXParent")
        if parent is None:
            break
        prole = _to_str(_copy(parent, A_ROLE))
        plabel = _label_of_handle(parent)
        if prole == "AXApplication":
            app = plabel
            break
        if prole == "AXWindow" and not window:
            window = plabel
        elif plabel and len(ancestry) < 6:
            ancestry.append(f"{prole.removeprefix('AX')} '{plabel[:60]}'")
        node = parent
    return {"element": asdict(info), "ancestry": ancestry, "window": window, "app": app,
            "url": url if url.startswith(("http://", "https://", "file://")) else ""}


def handle_label(handle: Any) -> str | None:
    """The live label of a retained AXUIElement (None when it can't be read)."""
    if not _IMPORT_OK or handle is None:
        return None
    try:
        return (_to_str(_copy(handle, A_TITLE)) or _to_str(_copy(handle, A_DESC))
                or _to_str(_copy(handle, A_LABEL)) or _to_str(_copy(handle, A_VALUE)))
    except Exception:  # noqa: BLE001 — element gone
        return None


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


def focused_window_frame() -> tuple[float, float, float, float] | None:
    """(x, y, w, h) in global points of the frontmost app's focused window."""
    if not _IMPORT_OK:
        return None
    try:
        pid = frontmost_app().get("pid", -1)
        if pid is None or pid <= 0:
            return None
        win = _focused_window(AX.AXUIElementCreateApplication(pid))
        if win is None:
            return None
        pos = _value_pair(_copy(win, A_POSITION), "point")
        size = _value_pair(_copy(win, A_SIZE), "size")
        if not pos or not size or size[0] <= 0 or size[1] <= 0:
            return None
        return (pos[0], pos[1], size[0], size[1])
    except Exception:
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
                    identifier=_to_str(_copy(el, "AXIdentifier")),
                    help=_to_str(_copy(el, "AXHelp")),
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
