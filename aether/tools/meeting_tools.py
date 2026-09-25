"""Recall tools over meeting notes: find a meeting, read its notes and transcript."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..core.security import wrap_untrusted

if TYPE_CHECKING:
    from .registry import AgentContext, ToolSpec


def _store():  # noqa: ANN202
    from .. import meetings

    return meetings.get_store()


def _when(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _h_search(args: dict, _ctx: AgentContext) -> str:
    query = str(args.get("query") or "").strip()
    store = _store()
    if query:
        hits = store.search(query, limit=int(args.get("limit") or 8))
        if not hits:
            return f"No meeting transcript mentions '{query[:60]}'."
        lines = [f"[{h['id']}] {_when(h['started'])} · {h['title']} — {h['snippet']}"
                 for h in hits]
    else:
        rows = store.list(limit=int(args.get("limit") or 8))
        if not rows:
            return "No meetings recorded yet."
        lines = [f"[{r['id']}] {_when(r['started'])} · {r['title']} — "
                 f"{(r['summary'] or 'no notes')[:200]}" for r in rows]
    return ("Meetings:\n" + wrap_untrusted("\n".join(lines), "meeting_notes")
            + "\nUse meeting_notes(id) for the notes and transcript of one.")


def _h_notes(args: dict, _ctx: AgentContext) -> str:
    from .. import meetings

    store = _store()
    m = store.meeting(str(args.get("id") or ""))
    if m is None:
        return "ERROR: no meeting with that id (search_meetings lists them)."
    parts = []
    if m.get("notes"):
        parts.append(meetings.render_notes(m["title"], m["started"], m["notes"]))
    transcript = meetings.transcript_for_model(store.segments(m["id"]), m["started"])
    parts.append("Transcript:\n" + (transcript[-6000:] or "(empty)"))
    return f"{m['title']} ({_when(m['started'])}):\n" + wrap_untrusted("\n\n".join(parts),
                                                                      "meeting_notes")


def specs() -> list[ToolSpec]:
    from .registry import ToolSpec

    return [
        ToolSpec(
            name="search_meetings",
            description=("Find meetings the user took notes in: by words said in them, or the "
                         "most recent ones when query is empty. For 'what did we decide about "
                         "X', 'my action items from Monday's call'."),
            json_schema={"type": "object", "properties": {
                "query": {"type": "string"}, "limit": {"type": "integer"}}},
            permission="none", impact="read", handler=_h_search),
        ToolSpec(
            name="meeting_notes",
            description="The notes (summary, decisions, action items) and transcript of one "
                        "meeting, by the id from search_meetings.",
            json_schema={"type": "object", "properties": {"id": {"type": "string"}},
                         "required": ["id"]},
            permission="none", impact="read", handler=_h_notes),
    ]


def describe(name: str, args: dict) -> str | None:
    if name == "search_meetings":
        q = str(args.get("query") or "")
        return f"search meetings for '{q[:40]}'" if q else "list recent meetings"
    if name == "meeting_notes":
        return f"read meeting notes {args.get('id')}"
    return None
