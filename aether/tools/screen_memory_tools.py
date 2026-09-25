"""Recall tools over screen memory: what was on screen, and when."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..core.security import wrap_untrusted

if TYPE_CHECKING:
    from .registry import AgentContext, ToolSpec

OFF = ("Screen memory is off, so there is nothing to search. The user can turn it on with "
       "screen_memory.enabled in config.yaml.")


def _recorder():  # noqa: ANN202
    from .. import screen_memory

    return screen_memory.get()


def _h_search_screen(args: dict, _ctx: AgentContext) -> str:
    rec = _recorder()
    if rec is None:
        return OFF
    hours = float(args.get("since_hours") or 24)
    since = time.time() - hours * 3600
    hits = rec.store.search(str(args.get("query") or ""), since=since,
                            app=str(args.get("app") or ""), limit=int(args.get("limit") or 8))
    if not hits:
        return f"Nothing on screen in the last {hours:g} h matched."
    lines = []
    for h in hits:
        d = h.as_dict()
        lines.append(f"[{d['id']}] {d['time']} · {d['app']} · '{d['window'][:60]}' — "
                     f"{d['snippet'][:240]}")
    return (f"Screen memory, last {hours:g} h ({len(hits)} match(es)):\n"
            + wrap_untrusted("\n".join(lines), "screen_memory")
            + "\nUse screen_memory_detail(id) for the full text of one.")


def _h_detail(args: dict, _ctx: AgentContext) -> str:
    rec = _recorder()
    if rec is None:
        return OFF
    cap = rec.store.get(int(args.get("id") or 0))
    if cap is None:
        return "ERROR: no capture with that id."
    d = cap.as_dict()
    return (f"{d['time']} · {d['app']} · '{d['window'][:80]}':\n"
            + wrap_untrusted(cap.text[:3000], "screen_memory"))


def _h_activity(args: dict, _ctx: AgentContext) -> str:
    rec = _recorder()
    if rec is None:
        return OFF
    hours = float(args.get("hours") or 8)
    apps = rec.store.activity(time.time() - hours * 3600)
    if not apps:
        return f"No screen activity recorded in the last {hours:g} h."
    fmt = lambda t: time.strftime("%H:%M", time.localtime(t))  # noqa: E731
    lines = []
    for a in apps[:12]:
        windows = "; ".join(w[:50] for w in a["windows"][:3])
        lines.append(f"- {a['app']}: {fmt(a['first'])}–{fmt(a['last'])} ({windows})")
    return f"Activity in the last {hours:g} h:\n" + wrap_untrusted("\n".join(lines),
                                                                  "screen_memory")


def specs() -> list[ToolSpec]:
    from .registry import ToolSpec

    return [
        ToolSpec(
            name="search_screen",
            description=("Search what the user has had on screen (screen memory, if they "
                         "turned it on): words from a page, a message, a document. Use for "
                         "'what was that article I was reading', 'find the number from the "
                         "email earlier'."),
            json_schema={"type": "object", "properties": {
                "query": {"type": "string"},
                "since_hours": {"type": "number", "description": "how far back (default 24)"},
                "app": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"]},
            permission="screen", impact="read", handler=_h_search_screen),
        ToolSpec(
            name="screen_memory_detail",
            description="The full text of one screen memory capture (an id from search_screen).",
            json_schema={"type": "object", "properties": {"id": {"type": "integer"}},
                         "required": ["id"]},
            permission="screen", impact="read", handler=_h_detail),
        ToolSpec(
            name="activity_summary",
            description=("Which apps and windows the user worked in over the last hours "
                         "(from screen memory)."),
            json_schema={"type": "object", "properties": {"hours": {"type": "number"}}},
            permission="screen", impact="read", handler=_h_activity),
    ]


def describe(name: str, args: dict) -> str | None:
    if name == "search_screen":
        return f"search screen memory for '{str(args.get('query', ''))[:40]}'"
    if name == "screen_memory_detail":
        return f"read screen memory {args.get('id')}"
    if name == "activity_summary":
        return "summarize recent activity"
    return None
