"""aether/tools/integration_tools.py: each tool with a faked native_effector.pim
or a faked run_applescript_args, the disabled-integration message, and the
'Aether app must be running' fallback."""
from __future__ import annotations

import pytest

from aether import integrations
from aether.integrations import apple
from aether.ipc import native_effector
from aether.tools import integration_tools as it
from aether.tools.registry import DEFAULT_REGISTRY, AgentContext


@pytest.fixture(autouse=True)
def _isolated_integrations(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("AETHER_DATA_DIR", str(tmp_path))


def _enable(*ids: str) -> None:
    for i in ids:
        integrations.set_enabled(i, True)


@pytest.mark.unit
class TestWhen:
    def test_naive_is_local_time(self) -> None:
        import time as _time
        from datetime import datetime

        epoch = it._when("2026-09-26T14:00")
        assert epoch == datetime(2026, 9, 26, 14, 0).timestamp()
        assert _time.localtime(epoch).tm_hour == 14

    def test_bad_value_raises_clear_message(self) -> None:
        with pytest.raises(ValueError, match="ISO 8601"):
            it._when("not a date")


@pytest.mark.unit
class TestDisabled:
    @pytest.mark.parametrize(("tool", "args"), [
        ("calendar_events", {}),
        ("reminders_list", {}),
        ("contacts_search", {"query": "sam"}),
        ("notes_search", {"query": "x"}),
        ("mail_search", {"query": "x"}),
        ("calendar_create_event", {"title": "T", "start": "2026-09-26T09:00",
                                   "end": "2026-09-26T09:30"}),
        ("reminders_add", {"title": "T"}),
        ("notes_create", {"title": "T", "body": "b"}),
    ])
    def test_disabled_message(self, tool, args) -> None:  # noqa: ANN001
        out = DEFAULT_REGISTRY.dispatch(tool, args, AgentContext())
        assert "connect it in the Aether window under Integrations" in out


@pytest.mark.unit
class TestEventKitTools:
    def test_calendar_events_wraps_and_formats(self, monkeypatch) -> None:  # noqa: ANN001
        _enable("calendar")
        calls = {}

        def fake_pim(action, args=None):  # noqa: ANN001, ANN202
            calls["action"], calls["args"] = action, args
            return [{"id": "1", "title": "Standup", "start": 1790431200.0,
                     "end": 1790434800.0, "location": "Zoom", "calendar": "Work",
                     "notes": "weekly sync", "all_day": False}]

        monkeypatch.setattr(native_effector, "pim", fake_pim)
        out = DEFAULT_REGISTRY.dispatch("calendar_events", {}, AgentContext())
        assert calls["action"] == "events"
        assert "<calendar>" in out and "Standup" in out and "weekly sync" in out
        assert "UNTRUSTED" in out

    def test_calendar_events_bad_dates(self) -> None:
        _enable("calendar")
        out = DEFAULT_REGISTRY.dispatch("calendar_events", {"start": "nope"}, AgentContext())
        assert "ISO 8601" in out

    def test_reminders_list_empty(self, monkeypatch) -> None:  # noqa: ANN001
        _enable("reminders")
        monkeypatch.setattr(native_effector, "pim", lambda action, args=None: [])
        out = DEFAULT_REGISTRY.dispatch("reminders_list", {}, AgentContext())
        assert out == "No reminders."

    def test_contacts_search_requires_query(self) -> None:
        _enable("contacts")
        out = DEFAULT_REGISTRY.dispatch("contacts_search", {}, AgentContext())
        assert out.startswith("ERROR")

    def test_contacts_search_wraps(self, monkeypatch) -> None:  # noqa: ANN001
        _enable("contacts")
        monkeypatch.setattr(native_effector, "pim", lambda action, args=None: [
            {"name": "Sam Lee", "organization": "Acme", "emails": ["sam@acme.com"],
             "phones": ["555-1234"]}])
        out = DEFAULT_REGISTRY.dispatch("contacts_search", {"query": "sam"}, AgentContext())
        assert "Sam Lee" in out and "Acme" in out and "<contacts>" in out

    def test_app_must_be_running_when_unreachable(self, monkeypatch) -> None:  # noqa: ANN001
        _enable("calendar")

        def fake_pim(action, args=None):  # noqa: ANN001, ANN202
            raise OSError("connection refused")

        monkeypatch.setattr(native_effector, "pim", fake_pim)
        out = DEFAULT_REGISTRY.dispatch("calendar_events", {}, AgentContext())
        assert "Aether app must be running" in out

    def test_permission_error_passed_through(self, monkeypatch) -> None:  # noqa: ANN001
        _enable("calendar")

        def fake_pim(action, args=None):  # noqa: ANN001, ANN202
            raise RuntimeError("Calendar access isn't allowed — connect it in "
                               "Aether → Integrations")

        monkeypatch.setattr(native_effector, "pim", fake_pim)
        out = DEFAULT_REGISTRY.dispatch("calendar_events", {}, AgentContext())
        assert "Calendar access isn't allowed" in out

    def test_calendar_create_event_success(self, monkeypatch) -> None:  # noqa: ANN001
        _enable("calendar")
        captured = {}

        def fake_pim(action, args=None):  # noqa: ANN001, ANN202
            captured["action"], captured["args"] = action, args
            return {"id": "9", "title": args["title"], "start": args["start"],
                    "end": args["end"], "location": args["location"],
                    "calendar": args["calendar"] or "Home", "notes": args["notes"],
                    "all_day": False}

        monkeypatch.setattr(native_effector, "pim", fake_pim)
        out = DEFAULT_REGISTRY.dispatch("calendar_create_event", {
            "title": "Standup", "start": "2026-09-26T09:00", "end": "2026-09-26T09:30",
            "location": "Zoom"}, AgentContext())
        assert captured["action"] == "create_event"
        assert captured["args"]["title"] == "Standup"
        assert "Created" in out and "Standup" in out

    def test_reminders_add_no_due(self, monkeypatch) -> None:  # noqa: ANN001
        _enable("reminders")
        captured = {}

        def fake_pim(action, args=None):  # noqa: ANN001, ANN202
            captured["args"] = args
            return {"id": "2", "title": args["title"]}

        monkeypatch.setattr(native_effector, "pim", fake_pim)
        out = DEFAULT_REGISTRY.dispatch("reminders_add", {"title": "Buy milk"}, AgentContext())
        assert captured["args"]["due"] is None
        assert "Added" in out


@pytest.mark.unit
class TestAppleScriptTools:
    def test_notes_search_wraps(self, monkeypatch) -> None:  # noqa: ANN001
        _enable("notes")
        monkeypatch.setattr(apple, "notes_search",
                            lambda query, limit=10: [{"name": "N", "modified": "today",
                                                      "snippet": "hello"}])
        out = DEFAULT_REGISTRY.dispatch("notes_search", {"query": "x"}, AgentContext())
        assert "<notes>" in out and "hello" in out

    def test_notes_search_applescript_error(self, monkeypatch) -> None:  # noqa: ANN001
        _enable("notes")

        def boom(query, limit=10):  # noqa: ANN001, ANN202
            raise apple.AppleScriptError("Couldn't search Notes: not allowed.")

        monkeypatch.setattr(apple, "notes_search", boom)
        out = DEFAULT_REGISTRY.dispatch("notes_search", {"query": "x"}, AgentContext())
        assert out.startswith("ERROR")

    def test_notes_create_success(self, monkeypatch) -> None:  # noqa: ANN001
        _enable("notes")
        captured = {}

        def fake_create(title, body, folder=""):  # noqa: ANN001, ANN202
            captured["title"], captured["body"], captured["folder"] = title, body, folder
            return "OK"

        monkeypatch.setattr(apple, "notes_create", fake_create)
        out = DEFAULT_REGISTRY.dispatch(
            "notes_create", {"title": "Ideas", "body": "b", "folder": "Work"}, AgentContext())
        assert captured == {"title": "Ideas", "body": "b", "folder": "Work"}
        assert "Created note 'Ideas'" in out

    def test_mail_search_wraps(self, monkeypatch) -> None:  # noqa: ANN001
        _enable("mail")
        monkeypatch.setattr(apple, "mail_search",
                            lambda query, limit=10, mailbox="": [
                                {"subject": "Hi", "sender": "sam@x.com", "date": "today",
                                 "snippet": "see you"}])
        out = DEFAULT_REGISTRY.dispatch("mail_search", {"query": "x"}, AgentContext())
        assert "<mail>" in out and "see you" in out
