"""The general pasteboard: read, write, snapshot/restore, paste-and-restore.

Pasting is how long or non-ASCII text gets into apps reliably (and fast). The
user's clipboard is snapshotted first and restored afterwards, but only if
nobody changed it in between (the yoclicky pattern: compare changeCount).
"""
from __future__ import annotations

import subprocess
import time
from typing import Any

try:
    from AppKit import NSPasteboard, NSPasteboardTypeString
    _OK = True
except Exception:  # pragma: no cover — non-macOS
    _OK = False


def _pb():  # noqa: ANN202
    return NSPasteboard.generalPasteboard()


def change_count() -> int:
    return int(_pb().changeCount()) if _OK else 0


def get_text() -> str:
    if _OK:
        value = _pb().stringForType_(NSPasteboardTypeString)
        return str(value) if value is not None else ""
    out = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
    return out.stdout


def set_text(text: str) -> int:
    """Put text on the clipboard; returns the new changeCount."""
    if _OK:
        pb = _pb()
        pb.clearContents()
        pb.setString_forType_(str(text), NSPasteboardTypeString)
        return int(pb.changeCount())
    subprocess.run(["pbcopy"], input=str(text), text=True, timeout=5, check=True)
    return 0


def snapshot() -> list[dict[str, Any]]:
    """Every item and every type on the clipboard (rich text, images, files)."""
    if not _OK:
        return [{"public.utf8-plain-text": get_text()}]
    items: list[dict[str, Any]] = []
    for item in _pb().pasteboardItems() or []:
        entry: dict[str, Any] = {}
        for t in item.types() or []:
            data = item.dataForType_(t)
            if data is not None:
                entry[str(t)] = data
        items.append(entry)
    return items


def restore(items: list[dict[str, Any]]) -> None:
    if not _OK:
        if items:
            set_text(str(items[0].get("public.utf8-plain-text", "")))
        return
    from AppKit import NSPasteboardItem

    pb = _pb()
    pb.clearContents()
    objs = []
    for entry in items:
        it = NSPasteboardItem.alloc().init()
        for t, data in entry.items():
            it.setData_forType_(data, t)
        objs.append(it)
    if objs:
        pb.writeObjects_(objs)


def paste_text(text: str, *, restore_after: float = 0.6) -> str:
    """Paste text into the focused field with ⌘V, then restore the clipboard."""
    from . import input as kbd

    saved = snapshot()
    ours = set_text(text)
    time.sleep(0.05)
    kbd.press_key("v", modifiers=["cmd"])
    time.sleep(max(restore_after, 0.0))
    if not _OK or change_count() == ours:
        restore(saved)
    return f"Pasted {len(text)} characters."
