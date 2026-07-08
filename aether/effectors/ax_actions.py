"""Accessibility actions — AXPress / set value (preferred over pixel clicks)."""
from __future__ import annotations

from typing import Any

from ..perception import accessibility as ax

try:
    import ApplicationServices as AX
    _OK = ax.available()
except Exception:  # pragma: no cover
    _OK = False

# Cache AXUIElement handles keyed by element index (refreshed each tree read).
_element_handles: dict[int, Any] = {}
# Per-app namespaces for background apps (refreshed by get_app_context).
_app_handles: dict[str, dict[int, Any]] = {}


def clear_handles() -> None:
    _element_handles.clear()


def register_handles(handles: dict[int, Any]) -> None:
    _element_handles.clear()
    _element_handles.update(handles)


def register_app_handles(app_name: str, handles: dict[int, Any]) -> None:
    _app_handles[app_name.lower()] = dict(handles)


def app_handle(app_name: str, element_index: int) -> Any | None:
    return _app_handles.get(app_name.lower(), {}).get(int(element_index))


def _press_action_const():
    for name in ("kAXPressAction", "AXPressAction"):
        v = getattr(AX, name, None)
        if v is not None:
            return v
    return "AXPress"


def can_press(element_index: int) -> bool:
    return element_index in _element_handles and _OK


def press_element(element_index: int) -> str:
    """Perform AXPress on an element by index from the last AX read."""
    if not _OK:
        raise RuntimeError("Accessibility unavailable.")
    el = _element_handles.get(int(element_index))
    if el is None:
        raise ValueError(
            f"element_index {element_index} not found — call get_screen_context again."
        )
    action = _press_action_const()
    err = AX.AXUIElementPerformAction(el, action)
    if err != 0:
        raise RuntimeError(f"AXPress failed (error {err}) for element [{element_index}]")
    return f"AXPress on element [{element_index}]."


def set_value(element_index: int, value: str) -> str:
    """Set AXValue on a text field."""
    if not _OK:
        raise RuntimeError("Accessibility unavailable.")
    el = _element_handles.get(int(element_index))
    if el is None:
        raise ValueError(f"element_index {element_index} not found.")
    err = AX.AXUIElementSetAttributeValue(el, ax.A_VALUE, value)
    if err != 0:
        raise RuntimeError(f"AX set value failed (error {err})")
    return f"Set value on element [{element_index}] ({len(value)} chars)."


# -- background-app actions on retained handles (AX ignores focus) --

def press_handle(handle: Any, label: str = "") -> str:
    """AXPress a retained AXUIElement from any app, focused or not."""
    if not _OK:
        raise RuntimeError("Accessibility unavailable.")
    err = AX.AXUIElementPerformAction(handle, _press_action_const())
    if err != 0:
        raise RuntimeError(f"background AXPress failed (error {err}) {label}")
    return f"AXPress {label} (background)."


def set_value_handle(handle: Any, value: str, label: str = "") -> str:
    """Set AXValue on a retained AXUIElement from any app."""
    if not _OK:
        raise RuntimeError("Accessibility unavailable.")
    err = AX.AXUIElementSetAttributeValue(handle, ax.A_VALUE, value)
    if err != 0:
        raise RuntimeError(f"background AX set value failed (error {err}) {label}")
    return f"Set value {label} ({len(value)} chars, background)."
