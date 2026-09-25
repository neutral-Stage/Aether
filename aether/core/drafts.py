"""Outgoing messages as editable drafts (VoiceOS's idea).

When a confirmed action sends something to other people (an email, a chat
message, an event, an issue), the confirmation shows its fields as an
editable draft instead of a yes/no. The user can fix the wording, the
recipients or the time before it goes out; their edits replace the model's
values, and the audit log records which fields they changed.
"""
from __future__ import annotations

import re
from typing import Any

# In the order they are shown.
DRAFT_KEYS = ("to", "recipient", "recipients", "cc", "bcc", "channel", "subject", "title",
              "summary", "start", "end", "due", "location", "list", "calendar", "folder",
              "notes", "body", "text", "message", "content", "description", "comment")
LONG_KEYS = frozenset({"body", "text", "message", "content", "description", "comment", "notes"})
MAX_FIELD = 20_000
_OUTBOUND_WORDS = frozenset({"send", "post", "reply", "message", "email", "comment", "publish",
                             "tweet", "invite", "schedule", "chat", "forward", "share"})
_CREATED = frozenset({"event", "issue", "task", "page", "ticket", "meeting"})
# aether/tools/integration_tools.py writes — always confirmed with a draft
# (see aether/core/policy.py's _ALWAYS_CONFIRM), never sent by an mcp_ name.
_INTEGRATION_WRITES = frozenset({"calendar_create_event", "reminders_add", "notes_create"})


def is_outbound(tool: str) -> bool:
    """Tools whose effect reaches other people (MCP integrations, mail drafts).
    Matched on whole words of the name ("gmail_search" is not "mail")."""
    if tool == "mail_compose" or tool in _INTEGRATION_WRITES:
        return True
    if not tool.startswith("mcp_"):
        return False
    words = [w for w in re.split(r"[_\W]+", tool.lower()) if w]
    if _OUTBOUND_WORDS.intersection(words):
        return True
    return any(a == "create" and b in _CREATED for a, b in zip(words, words[1:]))


def draft_fields(tool: str, args: dict[str, Any]) -> list[dict[str, str]] | None:
    """The editable fields of an outgoing action, or None when it isn't one."""
    if not is_outbound(tool):
        return None
    fields = [{"key": k, "value": args[k], "long": k in LONG_KEYS}
              for k in DRAFT_KEYS
              if isinstance(args.get(k), str) and len(args[k]) <= MAX_FIELD]
    return fields or None


def apply_edits(args: dict[str, Any], fields: list[dict[str, Any]],
                edits: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    """Args with the user's edits (only for fields that were shown)."""
    if not edits:
        return args, []
    shown = {f["key"] for f in fields}
    new = dict(args)
    changed = []
    for key, value in edits.items():
        if key in shown and isinstance(value, str) and value != args.get(key):
            new[key] = value[:MAX_FIELD]
            changed.append(key)
    return new, changed
