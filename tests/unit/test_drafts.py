"""Outgoing messages as editable drafts in the confirmation."""
from __future__ import annotations

import asyncio

import pytest

from aether.core import drafts
from aether.tools.registry import ToolSpec


def test_outbound_detection_and_fields() -> None:
    assert drafts.is_outbound("mcp_gmail_send_email")
    assert drafts.is_outbound("mcp_slack_post_message")
    assert drafts.is_outbound("mcp_calendar_create_event") and drafts.is_outbound("mail_compose")
    assert not drafts.is_outbound("mcp_gmail_search") and not drafts.is_outbound("run_shell")
    fields = drafts.draft_fields("mcp_gmail_send_email", {
        "body": "Hi Sam", "to": "sam@example.com", "subject": "Lunch", "priority": 3})
    assert [f["key"] for f in fields] == ["to", "subject", "body"]
    assert fields[2]["long"] and not fields[0]["long"]
    assert drafts.draft_fields("mcp_gmail_search", {"query": "x"}) is None
    assert drafts.draft_fields("mcp_slack_post_message", {"n": 1}) is None


def test_integration_writes_are_outbound_with_ordered_fields() -> None:
    for name in ("calendar_create_event", "reminders_add", "notes_create"):
        assert drafts.is_outbound(name)
    fields = drafts.draft_fields("calendar_create_event", {
        "title": "Standup", "start": "2026-09-26T09:00", "end": "2026-09-26T09:30",
        "location": "Zoom", "calendar": "Work", "notes": "weekly sync"})
    assert [f["key"] for f in fields] == ["title", "start", "end", "location", "calendar",
                                          "notes"]
    fields = drafts.draft_fields("reminders_add", {
        "title": "Buy milk", "due": "2026-09-26T18:00", "list": "Errands", "notes": "2%"})
    assert [f["key"] for f in fields] == ["title", "due", "list", "notes"]
    fields = drafts.draft_fields("notes_create", {
        "title": "Ideas", "body": "long body text", "folder": "Work"})
    assert [f["key"] for f in fields] == ["title", "folder", "body"]
    assert fields[2]["long"] and not fields[0]["long"]


def test_apply_edits_only_touches_shown_fields() -> None:
    args = {"to": "sam@example.com", "body": "Hi", "attachment": "/etc/passwd"}
    fields = drafts.draft_fields("mcp_gmail_send_email", args)
    new, changed = drafts.apply_edits(args, fields, {"body": "Hi Sam!", "attachment": "/x",
                                                    "to": "sam@example.com"})
    assert new["body"] == "Hi Sam!" and new["attachment"] == "/etc/passwd" and changed == ["body"]
    assert drafts.apply_edits(args, fields, None) == (args, [])


def test_confirmation_bridge_returns_edits() -> None:
    from sidecar import confirmation

    sent = []

    async def broadcast(ev):  # noqa: ANN001, ANN202
        sent.append(ev)

    async def scenario():  # noqa: ANN202
        confirmation.set_broadcaster(broadcast)
        task = asyncio.ensure_future(confirmation.request_draft_confirmation(
            "send email", [{"key": "body", "value": "Hi", "long": True}]))
        await asyncio.sleep(0.01)
        rid = sent[-1]["request_id"]
        assert sent[-1]["draft"][0]["key"] == "body"
        assert confirmation.resolve_confirmation(rid, True, {"body": "Hi Sam"})
        first = await task
        plain = asyncio.ensure_future(confirmation.request_confirmation("delete x"))
        await asyncio.sleep(0.01)
        assert "draft" not in sent[-1]
        confirmation.resolve_confirmation(sent[-1]["request_id"], False, {"x": "y"})
        return first, await plain

    try:
        assert asyncio.run(scenario()) == ((True, {"body": "Hi Sam"}), False)
    finally:
        confirmation.set_broadcaster(None)


def test_agent_sends_the_edited_draft(minimal_config, monkeypatch) -> None:  # noqa: ANN001
    from aether.core.orchestrator import Agent

    agent = Agent(minimal_config, hud=None)
    sent_args = {}

    def handler(args, ctx):  # noqa: ANN001, ANN202
        sent_args.update(args)
        return "sent"

    agent.registry.register(ToolSpec("mcp_mail_send_email", {"type": "object", "properties": {
        "to": {"type": "string"}, "body": {"type": "string"}}}, "network", "destructive",
        "send", handler))
    shown = {}

    async def draft_hook(description, draft):  # noqa: ANN001, ANN202
        shown["draft"] = draft
        return True, {"body": "Hi Sam, see you at 1."}

    agent.confirm_draft_async = draft_hook
    try:
        out = asyncio.run(agent._execute_call(  # noqa: SLF001
            "mcp_mail_send_email", {"to": "sam@example.com", "body": "Hi"}, step=1, rid="r"))
    finally:
        agent.registry.unregister("mcp_mail_send_email")
    assert [f["key"] for f in shown["draft"]] == ["to", "body"]
    assert sent_args["body"] == "Hi Sam, see you at 1."
    assert "The user edited body" in out.content and out.content.endswith("sent")


def test_confirmation_send_failure_leaves_nothing_pending() -> None:
    from sidecar import confirmation

    async def broken(ev):  # noqa: ANN001, ANN202
        raise ConnectionError("app went away")

    confirmation.set_broadcaster(broken)
    try:
        with pytest.raises(ConnectionError):
            asyncio.run(confirmation.request_confirmation("delete x"))
        assert confirmation.pending_count() == 0
    finally:
        confirmation.set_broadcaster(None)
