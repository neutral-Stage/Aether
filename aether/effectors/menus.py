"""Menu-bar control through Accessibility: list menus, press an item by path.

Menus are where Mac apps keep their full command set, with keyboard shortcuts
attached — pressing "File > Export as PDF…" by name is far more reliable than
hunting for a toolbar button in pixels. AXPress works on menu items of
background apps too. Falls back to System Events AppleScript.
"""
from __future__ import annotations

import re

from ..perception import accessibility as ax

try:
    import ApplicationServices as AX
    _OK = ax.available()
except Exception:  # pragma: no cover
    _OK = False

_ELLIPSIS_RE = re.compile(r"(\.\.\.|…)\s*$")
_MOD_SYMBOLS = {0: "⌘", 1: "⇧⌘", 2: "⌥⌘", 3: "⌥⇧⌘", 4: "⌃⌘", 5: "⌃⇧⌘", 6: "⌃⌥⌘",
                7: "⌃⌥⇧⌘", 8: ""}


def normalize_title(title: str) -> str:
    """'Export as PDF…' == 'export as pdf...' == 'Export As PDF'."""
    return _ELLIPSIS_RE.sub("", str(title or "")).strip().casefold()


def match_title(candidates: list[str], wanted: str) -> int | None:
    """Index of the best title match: exact, then prefix, then contains."""
    w = normalize_title(wanted)
    if not w:
        return None
    norm = [normalize_title(c) for c in candidates]
    for test in (lambda c: c == w, lambda c: c.startswith(w), lambda c: w in c):
        for i, c in enumerate(norm):
            if c and test(c):
                return i
    return None


def split_path(path: str | list[str]) -> list[str]:
    if isinstance(path, list):
        return [str(p).strip() for p in path if str(p).strip()]
    return [p.strip() for p in re.split(r"\s*(?:>|→|/)\s*", str(path or "")) if p.strip()]


def _children(el) -> list:  # noqa: ANN001
    return list(ax._copy(el, "AXChildren") or [])  # noqa: SLF001


def _title(el) -> str:  # noqa: ANN001
    return ax._to_str(ax._copy(el, "AXTitle"))  # noqa: SLF001


def _submenu_items(el) -> list:  # noqa: ANN001
    """Menu items under a menu-bar item or a submenu-bearing menu item."""
    items: list = []
    for child in _children(el):
        role = ax._to_str(ax._copy(child, "AXRole"))  # noqa: SLF001
        if role == "AXMenu":
            items.extend(_children(child))
        elif role == "AXMenuItem":
            items.append(child)
    return items


def _shortcut(el) -> str:  # noqa: ANN001
    char = ax._to_str(ax._copy(el, "AXMenuItemCmdChar"))  # noqa: SLF001
    if not char:
        return ""
    mods = ax._copy(el, "AXMenuItemCmdModifiers")  # noqa: SLF001
    try:
        prefix = _MOD_SYMBOLS.get(int(mods), "")
    except (TypeError, ValueError):
        prefix = "⌘"
    return f"{prefix}{char}"


def _app_pid(app: str | None) -> tuple[int, str]:
    if app:
        info = ax.resolve_app(app)
        if info is None:
            raise RuntimeError(f"app not running: {app}")
        return int(info["pid"]), str(info["name"])
    info = ax.frontmost_app()
    return int(info["pid"]), str(info["name"])


def _menu_bar_items(pid: int) -> list:
    app_el = AX.AXUIElementCreateApplication(pid)
    bar = ax._copy(app_el, "AXMenuBar")  # noqa: SLF001
    return _children(bar) if bar is not None else []


def list_menus(app: str | None = None, menu: str | None = None, depth: int = 1) -> str:
    """Menu titles; with `menu`, that menu's items with shortcuts and state."""
    if not _OK:
        raise RuntimeError("Accessibility unavailable.")
    pid, name = _app_pid(app)
    bar_items = _menu_bar_items(pid)
    titles = [_title(b) for b in bar_items]
    if not menu:
        return f"{name} menus: " + " | ".join(t for t in titles if t)
    idx = match_title(titles, menu)
    if idx is None:
        return f"No menu '{menu}' in {name}. Menus: " + ", ".join(t for t in titles if t)
    lines = [f"{name} › {titles[idx]}:"]

    def walk(items: list, level: int) -> None:
        for it in items:
            title = _title(it)
            if not title:
                continue
            enabled = ax._copy(it, "AXEnabled")  # noqa: SLF001
            sub = _submenu_items(it)
            sc = _shortcut(it)
            flag = "" if enabled in (None, True) else " (disabled)"
            lines.append(f"{'  ' * level}- {title}{'  ' + sc if sc else ''}{flag}"
                         f"{'  ›' if sub else ''}")
            if sub and level < depth:
                walk(sub, level + 1)

    walk(_submenu_items(bar_items[idx]), 1)
    return "\n".join(lines)


def press_menu_item(path: str | list[str], app: str | None = None) -> str:
    """Press 'Menu > Item > Subitem' in the frontmost (or named) app."""
    parts = split_path(path)
    if len(parts) < 2:
        raise ValueError("menu path needs at least 'Menu > Item'")
    if not _OK:
        return _press_via_applescript(parts, app)
    pid, name = _app_pid(app)
    bar_items = _menu_bar_items(pid)
    idx = match_title([_title(b) for b in bar_items], parts[0])
    if idx is None:
        return _press_via_applescript(parts, app or name)
    current = bar_items[idx]
    trail = [_title(current)]
    for want in parts[1:]:
        items = _submenu_items(current)
        j = match_title([_title(i) for i in items], want)
        if j is None:
            options = ", ".join(t for t in (_title(i) for i in items) if t)[:400]
            return f"ERROR: no '{want}' under {' › '.join(trail)}. Items: {options}"
        current = items[j]
        trail.append(_title(current))
    enabled = ax._copy(current, "AXEnabled")  # noqa: SLF001
    if enabled is False:
        return f"ERROR: '{' › '.join(trail)}' is disabled right now."
    err = AX.AXUIElementPerformAction(current, "AXPress")
    if err != 0:
        return _press_via_applescript(parts, app or name)
    return f"Pressed menu {' › '.join(trail)} in {name}."


def _as_quote(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def applescript_for(parts: list[str], process: str) -> str:
    """System Events script clicking a nested menu path (for tests + fallback)."""
    ref = f"menu bar item {_as_quote(parts[0])} of menu bar 1"
    ref = f"menu {_as_quote(parts[0])} of {ref}"
    for sub in parts[1:-1]:
        ref = f"menu {_as_quote(sub)} of menu item {_as_quote(sub)} of {ref}"
    return (f'tell application "System Events" to tell process {_as_quote(process)} '
            f"to click menu item {_as_quote(parts[-1])} of {ref}")


def _press_via_applescript(parts: list[str], app: str | None) -> str:
    from .applescript import run_applescript

    process = app or ax.frontmost_app().get("name", "")
    if not process:
        return "ERROR: no app to target for the menu path."
    res = run_applescript(applescript_for(parts, process))
    if res.returncode != 0:
        return f"ERROR: menu {' > '.join(parts)} not pressed: {res.summary(300)}"
    return f"Pressed menu {' › '.join(parts)} in {process} (System Events)."
