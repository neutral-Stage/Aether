"""Calendar, Reminders, Contacts, Notes and Mail — read tools for all five,
and confirmed-draft write tools for calendar events, reminders and notes.

Calendar/Reminders/Contacts go through the Swift app's EventKit/Contacts
access over its loopback server (``aether/ipc/native_effector.py``); Notes and
Mail go straight from here through AppleScript (``aether/integrations/
apple.py``). No integration does anything until the user turns it on in the
Aether window's Integrations panel (``aether/integrations``), and the two
create tools always ask first with an editable draft (see
``aether/core/policy.py``'s ``_ALWAYS_CONFIRM`` and ``aether/core/drafts.py``).
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .. import integrations
from ..core.security import wrap_untrusted

if TYPE_CHECKING:
    from .registry import AgentContext, ToolSpec

_ISO_HINT = "ERROR: use ISO 8601 local time like 2026-09-26T14:00"

_DISABLED = {
    "calendar": "Calendar isn't connected — connect it in the Aether window under Integrations.",
    "reminders": "Reminders isn't connected — connect it in the Aether window under Integrations.",
    "contacts": "Contacts isn't connected — connect it in the Aether window under Integrations.",
    "notes": "Notes isn't connected — connect it in the Aether window under Integrations.",
    "mail": "Mail isn't connected — connect it in the Aether window under Integrations.",
}
_APP_NOT_RUNNING = "ERROR: the Aether app must be running for this."


def _when(s: str) -> float:
    """Epoch seconds for an ISO 8601 string (naive values are local time)."""
    try:
        return datetime.fromisoformat(str(s)).timestamp()
    except (ValueError, TypeError) as e:
        raise ValueError(_ISO_HINT) from e


def _pim(action: str, args: dict[str, Any]) -> tuple[Any, str | None]:
    """(result, None) on success, or (None, message) — connectivity failures
    become the generic 'app must be running' message; a structured refusal
    from the app (permission denied, bad args) is passed through as-is."""
    from ..ipc import native_effector

    try:
        return native_effector.pim(action, args), None
    except RuntimeError as e:
        return None, f"ERROR: {e}"
    except Exception:  # noqa: BLE001 — unreachable app, timeout, bad connection…
        return None, _APP_NOT_RUNNING


def _fmt_dt(epoch: Any) -> str:
    try:
        return time.strftime("%a %Y-%m-%d %H:%M", time.localtime(float(epoch)))
    except (TypeError, ValueError):
        return "?"


def _fmt_time(epoch: Any) -> str:
    try:
        return time.strftime("%H:%M", time.localtime(float(epoch)))
    except (TypeError, ValueError):
        return "?"


def _event_line(e: dict) -> str:
    title = str(e.get("title") or "(untitled)")
    eid = str(e.get("id") or "")
    if e.get("all_day"):
        when = f"{_fmt_dt(e.get('start')).rsplit(' ', 1)[0]} (all day)"
    else:
        when = f"{_fmt_dt(e.get('start'))}–{_fmt_time(e.get('end') or e.get('start'))}"
    loc = f" @ {e['location']}" if e.get("location") else ""
    cal = f" [{e['calendar']}]" if e.get("calendar") else ""
    notes = f" — {str(e['notes'])[:200]}" if e.get("notes") else ""
    return f"[{eid}] {title}{loc}{cal} {when}{notes}"


def _reminder_line(r: dict) -> str:
    title = str(r.get("title") or "(untitled)")
    rid = str(r.get("id") or "")
    due = f" due {_fmt_dt(r['due'])}" if r.get("due") else ""
    done = " (done)" if r.get("completed") else ""
    lst = f" [{r['list']}]" if r.get("list") else ""
    notes = f" — {str(r['notes'])[:200]}" if r.get("notes") else ""
    return f"[{rid}] {title}{lst}{due}{done}{notes}"


def _contact_line(c: dict) -> str:
    name = str(c.get("name") or "(unnamed)")
    org = f" · {c['organization']}" if c.get("organization") else ""
    emails = ", ".join(c.get("emails") or [])
    phones = ", ".join(c.get("phones") or [])
    tail = "; ".join(p for p in (emails, phones) if p)
    return f"{name}{org}" + (f": {tail}" if tail else "")


# --- read tools --------------------------------------------------------

def _h_calendar_events(args: dict, _ctx: AgentContext) -> str:
    if not integrations.enabled("calendar"):
        return _DISABLED["calendar"]
    now = time.time()
    try:
        start = _when(args["start"]) if args.get("start") else now
        end = _when(args["end"]) if args.get("end") else now + 7 * 86400
    except ValueError as e:
        return str(e)
    result, err = _pim("events", {"from": start, "to": end,
                                  "query": str(args.get("query") or ""),
                                  "limit": int(args.get("limit") or 20)})
    if err:
        return err
    rows = result if isinstance(result, list) else []
    if not rows:
        return "No events in that range."
    text = "\n".join(_event_line(e) for e in rows if isinstance(e, dict))
    return wrap_untrusted(text, "calendar")


def _h_reminders_list(args: dict, _ctx: AgentContext) -> str:
    if not integrations.enabled("reminders"):
        return _DISABLED["reminders"]
    result, err = _pim("reminders", {"list": str(args.get("list") or ""),
                                     "include_completed": bool(args.get("include_completed")),
                                     "limit": int(args.get("limit") or 20)})
    if err:
        return err
    rows = result if isinstance(result, list) else []
    if not rows:
        return "No reminders."
    text = "\n".join(_reminder_line(r) for r in rows if isinstance(r, dict))
    return wrap_untrusted(text, "reminders")


def _h_contacts_search(args: dict, _ctx: AgentContext) -> str:
    if not integrations.enabled("contacts"):
        return _DISABLED["contacts"]
    query = str(args.get("query") or "").strip()
    if not query:
        return "ERROR: query is required."
    result, err = _pim("contacts", {"query": query, "limit": int(args.get("limit") or 10)})
    if err:
        return err
    rows = result if isinstance(result, list) else []
    if not rows:
        return f"No contacts matching '{query}'."
    text = "\n".join(_contact_line(c) for c in rows if isinstance(c, dict))
    return wrap_untrusted(text, "contacts")


def _h_notes_search(args: dict, _ctx: AgentContext) -> str:
    if not integrations.enabled("notes"):
        return _DISABLED["notes"]
    from ..integrations import apple

    try:
        rows = apple.notes_search(str(args.get("query") or ""), limit=int(args.get("limit") or 10))
    except apple.AppleScriptError as e:
        return f"ERROR: {e}"
    if not rows:
        return "No matching notes."
    text = "\n".join(f"[{r['name']}] {r['modified']}: {r['snippet']}" for r in rows)
    return wrap_untrusted(text, "notes")


def _h_mail_search(args: dict, _ctx: AgentContext) -> str:
    if not integrations.enabled("mail"):
        return _DISABLED["mail"]
    from ..integrations import apple

    try:
        rows = apple.mail_search(str(args.get("query") or ""), limit=int(args.get("limit") or 10),
                                 mailbox=str(args.get("mailbox") or ""))
    except apple.AppleScriptError as e:
        return f"ERROR: {e}"
    if not rows:
        return "No matching mail."
    text = "\n".join(f"{r['subject']} — {r['sender']} ({r['date']}): {r['snippet']}"
                     for r in rows)
    return wrap_untrusted(text, "mail")


# --- write tools (always confirmed, with an editable draft) ------------

def _h_calendar_create_event(args: dict, _ctx: AgentContext) -> str:
    if not integrations.enabled("calendar"):
        return _DISABLED["calendar"]
    title = str(args.get("title") or "").strip()
    if not title:
        return "ERROR: title is required."
    try:
        start = _when(args["start"])
        end = _when(args["end"])
    except (KeyError, ValueError):
        return _ISO_HINT
    result, err = _pim("create_event", {
        "title": title, "start": start, "end": end,
        "location": str(args.get("location") or ""), "notes": str(args.get("notes") or ""),
        "calendar": str(args.get("calendar") or "")})
    if err:
        return err
    e = result if isinstance(result, dict) else {}
    return f"Created: {_event_line(e)}" if e else f"Created '{title}'."


def _h_reminders_add(args: dict, _ctx: AgentContext) -> str:
    if not integrations.enabled("reminders"):
        return _DISABLED["reminders"]
    title = str(args.get("title") or "").strip()
    if not title:
        return "ERROR: title is required."
    due = None
    if args.get("due"):
        try:
            due = _when(args["due"])
        except ValueError:
            return _ISO_HINT
    result, err = _pim("add_reminder", {
        "title": title, "due": due, "list": str(args.get("list") or ""),
        "notes": str(args.get("notes") or "")})
    if err:
        return err
    r = result if isinstance(result, dict) else {}
    return f"Added: {_reminder_line(r)}" if r else f"Added '{title}'."


def _h_notes_create(args: dict, _ctx: AgentContext) -> str:
    if not integrations.enabled("notes"):
        return _DISABLED["notes"]
    title = str(args.get("title") or "").strip()
    if not title:
        return "ERROR: title is required."
    from ..integrations import apple

    try:
        apple.notes_create(title, str(args.get("body") or ""), folder=str(args.get("folder") or ""))
    except apple.AppleScriptError as e:
        return f"ERROR: {e}"
    return f"Created note '{title}'."


def specs() -> list[ToolSpec]:
    from .registry import ToolSpec

    return [
        ToolSpec(
            name="calendar_events",
            description=("Read the user's calendar. start/end are ISO 8601 local time "
                         "(default: now through +7 days); query filters by title/location/"
                         "notes."),
            json_schema={"type": "object", "properties": {
                "start": {"type": "string"}, "end": {"type": "string"},
                "query": {"type": "string"}, "limit": {"type": "integer"}}},
            permission="calendar", impact="read", handler=_h_calendar_events),
        ToolSpec(
            name="reminders_list",
            description="Read the user's reminders, optionally from one named list.",
            json_schema={"type": "object", "properties": {
                "list": {"type": "string"}, "include_completed": {"type": "boolean"},
                "limit": {"type": "integer"}}},
            permission="calendar", impact="read", handler=_h_reminders_list),
        ToolSpec(
            name="contacts_search",
            description="Look up a contact by name: organization, emails, phone numbers.",
            json_schema={"type": "object", "properties": {
                "query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"]},
            permission="contacts", impact="read", handler=_h_contacts_search),
        ToolSpec(
            name="notes_search",
            description="Search the user's Notes by title or body.",
            json_schema={"type": "object", "properties": {
                "query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"]},
            permission="notes", impact="read", handler=_h_notes_search),
        ToolSpec(
            name="mail_search",
            description="Search Mail (inbox, or a named mailbox) by subject or sender.",
            json_schema={"type": "object", "properties": {
                "query": {"type": "string"}, "mailbox": {"type": "string"},
                "limit": {"type": "integer"}},
                "required": ["query"]},
            permission="mail", impact="read", handler=_h_mail_search),
        ToolSpec(
            name="calendar_create_event",
            description=("Add a calendar event. ALWAYS confirmed with an editable draft — "
                         "no invitations are sent (EventKit can't invite anyone). "
                         "start/end are ISO 8601 local time."),
            json_schema={"type": "object", "properties": {
                "title": {"type": "string"}, "start": {"type": "string"},
                "end": {"type": "string"}, "location": {"type": "string"},
                "notes": {"type": "string"}, "calendar": {"type": "string"}},
                "required": ["title", "start", "end"]},
            permission="calendar", impact="reversible", handler=_h_calendar_create_event),
        ToolSpec(
            name="reminders_add",
            description=("Add a reminder. ALWAYS confirmed with an editable draft. "
                         "due is ISO 8601 local time (optional)."),
            json_schema={"type": "object", "properties": {
                "title": {"type": "string"}, "due": {"type": "string"},
                "list": {"type": "string"}, "notes": {"type": "string"}},
                "required": ["title"]},
            permission="calendar", impact="reversible", handler=_h_reminders_add),
        ToolSpec(
            name="notes_create",
            description="Create a note. ALWAYS confirmed with an editable draft.",
            json_schema={"type": "object", "properties": {
                "title": {"type": "string"}, "body": {"type": "string"},
                "folder": {"type": "string"}},
                "required": ["title", "body"]},
            permission="notes", impact="reversible", handler=_h_notes_create),
    ]


def describe(name: str, args: dict) -> str | None:
    if name == "calendar_events":
        return "read calendar events"
    if name == "reminders_list":
        return "read reminders"
    if name == "contacts_search":
        return f"look up contact '{str(args.get('query', ''))[:40]}'"
    if name == "notes_search":
        return f"search notes for '{str(args.get('query', ''))[:40]}'"
    if name == "mail_search":
        return f"search mail for '{str(args.get('query', ''))[:40]}'"
    if name == "calendar_create_event":
        return f"add calendar event '{str(args.get('title', ''))[:40]}'"
    if name == "reminders_add":
        return f"add reminder '{str(args.get('title', ''))[:40]}'"
    if name == "notes_create":
        return f"create note '{str(args.get('title', ''))[:40]}'"
    return None
