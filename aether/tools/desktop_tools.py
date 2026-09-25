"""Desktop tools beyond click/type: scroll, drag, hover, wait, files, menus,
windows, clipboard, open, notify, selected text.

These close the gap between "can click a button" and "can operate any
program": scrolling to find things, dragging, menu commands by name, reading
and writing files, and waiting for the UI instead of guessing sleep times.
Every tool declares a permission (capability toggle) and an impact; the policy
gate refines impact per call (e.g. overwriting a file is destructive).
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..effectors import clipboard, executor, files, menus, system, windows
from ..effectors import input as kbd
from ..perception import accessibility as ax

if TYPE_CHECKING:
    from .registry import AgentContext, ToolSpec

MAX_WAIT_SEC = 30.0


def _point(args: dict, ctx: "AgentContext", prefix: str = "") -> tuple[float, float] | None:
    """Resolve {prefix}index / {prefix}x,{prefix}y (or element_index/x/y) to a point."""
    idx = args.get(f"{prefix}index", args.get("element_index") if not prefix else None)
    if idx is not None:
        for el in ctx.elements or []:
            if el.get("idx") == int(idx):
                return (el["x"] + el["w"] / 2.0, el["y"] + el["h"] / 2.0)
        raise ValueError(f"element {idx} not found — call get_screen_context again.")
    x, y = args.get(f"{prefix}x"), args.get(f"{prefix}y")
    if x is not None and y is not None:
        return (float(x), float(y))
    return None


def _h_scroll(args: dict, ctx: "AgentContext") -> str:
    direction = str(args.get("direction", "down")).lower()
    amount = max(1, min(int(args.get("amount") or 5), 50))
    dy = {"down": -amount, "up": amount}.get(direction, 0)
    dx = {"left": amount, "right": -amount}.get(direction, 0)
    if not (dx or dy):
        return "ERROR: direction must be up, down, left or right."
    pt = _point(args, ctx)
    if pt is None:
        frame = ax.focused_window_frame()
        if frame:
            pt = (frame[0] + frame[2] / 2.0, frame[1] + frame[3] / 2.0)
    with executor.HID_LOCK:
        kbd.scroll(dx=dx, dy=dy, x=pt[0] if pt else None, y=pt[1] if pt else None)
    time.sleep(0.25)
    where = f" at ({int(pt[0])},{int(pt[1])})" if pt else ""
    return f"Scrolled {direction} {amount} lines{where}. Call get_screen_context to see the result."


def _h_drag(args: dict, ctx: "AgentContext") -> str:
    start = _point(args, ctx, "from_")
    end = _point(args, ctx, "to_")
    if start is None or end is None:
        return "ERROR: drag needs from_index or from_x/from_y, and to_index or to_x/to_y."
    duration = max(0.1, min(float(args.get("duration") or 0.5), 3.0))
    with executor.HID_LOCK:
        kbd.drag(start[0], start[1], end[0], end[1], duration=duration)
    time.sleep(0.3)
    return (f"Dragged ({int(start[0])},{int(start[1])}) → ({int(end[0])},{int(end[1])}).")


def _h_hover(args: dict, ctx: "AgentContext") -> str:
    pt = _point(args, ctx)
    if pt is None:
        return "ERROR: hover needs element_index or x, y."
    with executor.HID_LOCK:
        kbd.hover(pt[0], pt[1])
    time.sleep(max(0.2, min(float(args.get("seconds") or 0.8), 5.0)))
    return f"Hovering at ({int(pt[0])},{int(pt[1])})."


def _screen_has_text(needle: str) -> bool:
    needle = needle.casefold()
    for el in ax.read_tree(max_elements=250, capture_handles=False):
        if needle in (el.title or "").casefold() or needle in (el.value or "").casefold():
            return True
    return False


def _h_wait(args: dict, ctx: "AgentContext") -> str:
    from ..core import stop as stop_ctl

    for_text = str(args.get("for_text") or "").strip()
    for_app = str(args.get("for_app") or "").strip()
    if not (for_text or for_app):
        secs = max(0.1, min(float(args.get("seconds") or 1.0), MAX_WAIT_SEC))
        end = time.monotonic() + secs
        while time.monotonic() < end:
            stop_ctl.check()
            time.sleep(min(0.25, end - time.monotonic()) if end > time.monotonic() else 0)
        return f"Waited {secs:.1f}s."
    timeout = max(0.5, min(float(args.get("timeout") or 10.0), MAX_WAIT_SEC))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stop_ctl.check()
        if for_app and for_app.casefold() in ax.frontmost_app().get("name", "").casefold():
            return f"{for_app} is frontmost."
        if for_text and _screen_has_text(for_text):
            return f"'{for_text}' is on screen."
        time.sleep(0.5)
    what = f"'{for_text}'" if for_text else f"{for_app} to come to the front"
    return f"Timed out after {timeout:.0f}s waiting for {what}."


def _h_read_file(args: dict, _ctx: "AgentContext") -> str:
    return files.read_file(args["path"], max_bytes=int(args.get("max_bytes") or files.MAX_READ_BYTES),
                           offset=int(args.get("offset") or 0))


def _h_write_file(args: dict, _ctx: "AgentContext") -> str:
    return files.write_file(args["path"], args.get("content", ""),
                            mode=str(args.get("mode") or "overwrite"))


def _h_list_dir(args: dict, _ctx: "AgentContext") -> str:
    return files.list_dir(args.get("path") or "~", pattern=args.get("pattern"),
                          show_hidden=bool(args.get("show_hidden", False)))


def _h_open_path(args: dict, _ctx: "AgentContext") -> str:
    return system.open_path(args["path"], app=args.get("app"))


def _h_open_url(args: dict, _ctx: "AgentContext") -> str:
    return system.open_url(args["url"])


def _h_clipboard_get(_args: dict, _ctx: "AgentContext") -> str:
    text = clipboard.get_text()
    if not text:
        return "The clipboard has no text."
    shown = text if len(text) <= 4000 else text[:4000] + f"\n…({len(text) - 4000} more chars)"
    return f"Clipboard text ({len(text)} chars):\n{shown}"


def _h_clipboard_set(args: dict, _ctx: "AgentContext") -> str:
    clipboard.set_text(str(args.get("text", "")))
    return f"Copied {len(str(args.get('text', '')))} characters to the clipboard."


def _h_menu_item(args: dict, _ctx: "AgentContext") -> str:
    res = menus.press_menu_item(args["path"], app=args.get("app"))
    time.sleep(0.4)
    return res


def _h_list_menus(args: dict, _ctx: "AgentContext") -> str:
    return menus.list_menus(app=args.get("app"), menu=args.get("menu"),
                            depth=2 if args.get("menu") else 1)


def _h_focus_window(args: dict, _ctx: "AgentContext") -> str:
    res = windows.focus_window(args["app"], title_contains=args.get("title_contains"))
    time.sleep(0.4)
    return res


def _h_set_window_frame(args: dict, _ctx: "AgentContext") -> str:
    return windows.set_window_frame(
        args["app"], x=args.get("x"), y=args.get("y"), width=args.get("width"),
        height=args.get("height"), title_contains=args.get("title_contains"))


def _h_get_selected_text(_args: dict, _ctx: "AgentContext") -> str:
    text = system.get_selected_text()
    if not text:
        return "Nothing is selected (or the focused field does not expose a selection)."
    shown = text if len(text) <= 4000 else text[:4000] + "\n…(truncated)"
    return f"Selected text ({len(text)} chars):\n{shown}"


def _h_notify(args: dict, _ctx: "AgentContext") -> str:
    return system.notify(str(args.get("title") or "Aether"), str(args.get("message", "")))


def _h_quit_app(args: dict, _ctx: "AgentContext") -> str:
    name = str(args.get("name") or "").strip()
    if not name:
        return "ERROR: name is required."
    return system.quit_app(name)


def _h_set_volume(args: dict, _ctx: "AgentContext") -> str:
    muted = args.get("muted")
    level = args.get("level")
    change = args.get("change")
    if muted is None and level is None and change is None:
        return "ERROR: pass level (0-100), change (e.g. -10) or muted."
    return system.set_volume(
        level=int(level) if level is not None else None,
        change=int(change) if change is not None else None,
        muted=bool(muted) if muted is not None else None)


def _h_agent_only(_args: dict, _ctx: "AgentContext") -> str:
    # batch_actions and ask_user are run by the agent loop itself (each batched
    # action goes through the policy gate; questions go to the app's panel).
    return "ERROR: this tool is only available inside an agent run."


def needs_paste(text: str) -> bool:
    """Long or non-BMP text goes in by paste: faster and exact."""
    return len(text) > 200 or any(ord(c) > 0xFFFF for c in text)


def describe(name: str, args: dict) -> str | None:
    """Step descriptions for the HUD/trace. Never echo file contents or clipboard text."""
    if name == "scroll":
        return f"scroll {args.get('direction', 'down')} {args.get('amount', 5)}"
    if name == "drag":
        return "drag"
    if name == "hover":
        return "hover"
    if name == "wait":
        if args.get("for_text"):
            return f"wait for '{str(args['for_text'])[:40]}'"
        if args.get("for_app"):
            return f"wait for {args['for_app']}"
        return f"wait {args.get('seconds', 1)}s"
    if name == "read_file":
        return f"read {str(args.get('path', ''))[:60]}"
    if name == "write_file":
        return f"write {str(args.get('path', ''))[:60]}"
    if name == "list_dir":
        return f"list {str(args.get('path', '~'))[:60]}"
    if name == "open_path":
        return f"open {str(args.get('path', ''))[:60]}"
    if name == "open_url":
        return f"open {str(args.get('url', ''))[:60]}"
    if name == "clipboard_get":
        return "read the clipboard"
    if name == "clipboard_set":
        return f"copy {len(str(args.get('text', '')))} chars to the clipboard"
    if name == "menu_item":
        return f"menu {' › '.join(menus.split_path(args.get('path', '')))}"
    if name == "list_menus":
        return f"list menus{' ' + str(args['menu']) if args.get('menu') else ''}"
    if name == "focus_window":
        return f"focus {args.get('app', '')}"
    if name == "set_window_frame":
        return f"move/resize {args.get('app', '')} window"
    if name == "get_selected_text":
        return "read the selected text"
    if name == "notify":
        return "show a notification"
    if name == "batch_actions":
        n = len(args.get("actions") or []) if isinstance(args.get("actions"), list) else 0
        return f"{n} actions in a row"
    if name == "ask_user":
        return f"ask: {str(args.get('question', ''))[:60]}"
    if name == "quit_app":
        return f"quit {args.get('name', '')}"
    if name == "set_volume":
        if args.get("muted") is not None:
            return "mute" if args.get("muted") else "unmute"
        if args.get("change") is not None:
            return f"volume {int(args['change']):+d}"
        return f"volume {args.get('level')}%"
    return None


def specs() -> list["ToolSpec"]:
    from .registry import ToolSpec

    point = {"element_index": {"type": "integer"}, "x": {"type": "number"},
             "y": {"type": "number"}}
    return [
        ToolSpec(
            name="scroll",
            description=("Scroll the view under an element or point (default: the front "
                         "window's center). Scroll before deciding something isn't there."),
            json_schema={"type": "object", "properties": {
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                "amount": {"type": "integer", "description": "lines, default 5"},
                **point}, "required": ["direction"]},
            permission="input", impact="reversible", handler=_h_scroll),
        ToolSpec(
            name="drag",
            description=("Drag from one element/point to another (move files, reorder, "
                         "select ranges, sliders)."),
            json_schema={"type": "object", "properties": {
                "from_index": {"type": "integer"}, "from_x": {"type": "number"},
                "from_y": {"type": "number"}, "to_index": {"type": "integer"},
                "to_x": {"type": "number"}, "to_y": {"type": "number"},
                "duration": {"type": "number"}}},
            permission="input", impact="reversible", handler=_h_drag),
        ToolSpec(
            name="hover",
            description="Move the pointer over an element without clicking (tooltips, hover menus).",
            json_schema={"type": "object", "properties": {**point, "seconds": {"type": "number"}}},
            permission="input", impact="reversible", handler=_h_hover),
        ToolSpec(
            name="wait",
            description=("Wait for the UI instead of guessing: until text appears on screen "
                         "(for_text), an app comes to the front (for_app), or a number of "
                         "seconds. Max 30 s."),
            json_schema={"type": "object", "properties": {
                "seconds": {"type": "number"}, "for_text": {"type": "string"},
                "for_app": {"type": "string"}, "timeout": {"type": "number"}}},
            permission="none", impact="read", handler=_h_wait),
        ToolSpec(
            name="read_file",
            description="Read a text file (size-capped; use offset to page through big files).",
            json_schema={"type": "object", "properties": {
                "path": {"type": "string"}, "offset": {"type": "integer"},
                "max_bytes": {"type": "integer"}}, "required": ["path"]},
            permission="files", impact="read", handler=_h_read_file),
        ToolSpec(
            name="write_file",
            description=("Write a text file. mode: create (new file only), append, or "
                         "overwrite (replacing an existing file needs confirmation)."),
            json_schema={"type": "object", "properties": {
                "path": {"type": "string"}, "content": {"type": "string"},
                "mode": {"type": "string", "enum": ["create", "append", "overwrite"]}},
                "required": ["path", "content"]},
            permission="files", impact="reversible", handler=_h_write_file),
        ToolSpec(
            name="list_dir",
            description="List a folder, newest first, optionally filtered by a glob pattern.",
            json_schema={"type": "object", "properties": {
                "path": {"type": "string"}, "pattern": {"type": "string"},
                "show_hidden": {"type": "boolean"}}, "required": ["path"]},
            permission="files", impact="read", handler=_h_list_dir),
        ToolSpec(
            name="open_path",
            description="Open a file or folder in its default app, or in a named app.",
            json_schema={"type": "object", "properties": {
                "path": {"type": "string"}, "app": {"type": "string"}}, "required": ["path"]},
            permission="files", impact="reversible", handler=_h_open_path),
        ToolSpec(
            name="open_url",
            description="Open a URL in the user's default browser.",
            json_schema={"type": "object", "properties": {"url": {"type": "string"}},
                         "required": ["url"]},
            permission="network", impact="reversible", handler=_h_open_url),
        ToolSpec(
            name="clipboard_get",
            description="Read the text on the clipboard.",
            json_schema={"type": "object", "properties": {}},
            permission="screen", impact="read", handler=_h_clipboard_get),
        ToolSpec(
            name="clipboard_set",
            description="Put text on the clipboard.",
            json_schema={"type": "object", "properties": {"text": {"type": "string"}},
                         "required": ["text"]},
            permission="input", impact="reversible", handler=_h_clipboard_set),
        ToolSpec(
            name="menu_item",
            description=("Press a menu command by name, e.g. path='File > Export as PDF…' "
                         "(works in background apps with app=). Prefer this over hunting "
                         "for toolbar buttons."),
            json_schema={"type": "object", "properties": {
                "path": {"type": "string", "description": "'Menu > Item > Subitem'"},
                "app": {"type": "string"}}, "required": ["path"]},
            permission="input", impact="reversible", handler=_h_menu_item),
        ToolSpec(
            name="list_menus",
            description=("List an app's menus, or one menu's items with their keyboard "
                         "shortcuts — the fastest way to learn what an app can do."),
            json_schema={"type": "object", "properties": {
                "app": {"type": "string"}, "menu": {"type": "string"}}},
            permission="screen", impact="read", handler=_h_list_menus),
        ToolSpec(
            name="focus_window",
            description="Bring an app (and optionally a window whose title contains text) to the front.",
            json_schema={"type": "object", "properties": {
                "app": {"type": "string"}, "title_contains": {"type": "string"}},
                "required": ["app"]},
            permission="input", impact="reversible", handler=_h_focus_window),
        ToolSpec(
            name="set_window_frame",
            description="Move and/or resize an app's window (screen points).",
            json_schema={"type": "object", "properties": {
                "app": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"},
                "width": {"type": "number"}, "height": {"type": "number"},
                "title_contains": {"type": "string"}}, "required": ["app"]},
            permission="input", impact="reversible", handler=_h_set_window_frame),
        ToolSpec(
            name="get_selected_text",
            description="Read the text the user has selected in the focused app.",
            json_schema={"type": "object", "properties": {}},
            permission="screen", impact="read", handler=_h_get_selected_text),
        ToolSpec(
            name="notify",
            description="Show a macOS notification (e.g. when a long task finishes).",
            json_schema={"type": "object", "properties": {
                "title": {"type": "string"}, "message": {"type": "string"}},
                "required": ["message"]},
            permission="none", impact="reversible", handler=_h_notify),
        ToolSpec(
            name="quit_app",
            description=("Quit an app normally by name (it still asks to save unsaved work). "
                         "Use instead of clicking its menu."),
            json_schema={"type": "object", "properties": {"name": {"type": "string"}},
                         "required": ["name"]},
            permission="input", impact="reversible", handler=_h_quit_app),
        ToolSpec(
            name="set_volume",
            description="Set the sound output volume: level 0-100, change (e.g. -10), or muted.",
            json_schema={"type": "object", "properties": {
                "level": {"type": "integer"}, "change": {"type": "integer"},
                "muted": {"type": "boolean"}}},
            permission="input", impact="reversible", handler=_h_set_volume),
        ToolSpec(
            name="batch_actions",
            description=("Do up to 5 simple UI actions in a row without looking in between, "
                         "e.g. click a field, type, press return. Each action is "
                         "{tool, args} using click, click_element, click_text, click_mark, "
                         "type_text, press_key, scroll, hover, drag, wait, menu_item or "
                         "focus_window. Stops at the first failure. Use only when you are "
                         "sure what each step does."),
            json_schema={"type": "object", "properties": {"actions": {
                "type": "array", "maxItems": 5, "items": {"type": "object", "properties": {
                    "tool": {"type": "string"}, "args": {"type": "object"}},
                    "required": ["tool", "args"]}}}, "required": ["actions"]},
            permission="input", impact="reversible", handler=_h_agent_only),
        ToolSpec(
            name="ask_user",
            description=("Ask the user a short question and wait for the answer, when the "
                         "request is ambiguous and a wrong guess would cost them something. "
                         "Offer options when there are a few obvious choices. Never ask for "
                         "passwords or codes."),
            json_schema={"type": "object", "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}, "maxItems": 6}},
                "required": ["question"]},
            permission="none", impact="read", handler=_h_agent_only),
    ]
