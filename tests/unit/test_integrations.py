"""The integrations catalog, its on/off persistence, and the Notes/Mail
AppleScript layer (argv passing, output parsing, permission errors)."""
from __future__ import annotations

import pytest

from aether import integrations
from aether.effectors.applescript import AppleScriptResult
from aether.integrations import apple


@pytest.mark.unit
class TestCatalog:
    def test_catalog_has_all_five_apps(self) -> None:
        ids = {e["id"] for e in integrations.CATALOG}
        assert ids == {"calendar", "reminders", "contacts", "notes", "mail"}
        for entry in integrations.CATALOG:
            assert entry["tools"]
            assert entry["kind"] in ("eventkit", "contacts", "applescript")

    def test_default_all_off(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setenv("AETHER_DATA_DIR", str(tmp_path))
        for entry in integrations.catalog_status():
            assert entry["enabled"] is False
        assert not integrations.enabled("calendar")

    def test_set_enabled_persists(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setenv("AETHER_DATA_DIR", str(tmp_path))
        entry = integrations.set_enabled("calendar", True)
        assert entry["enabled"] is True and entry["name"] == "Calendar"
        assert integrations.enabled("calendar")
        assert (tmp_path / integrations.STATE_NAME).exists()
        # Other integrations are untouched.
        assert not integrations.enabled("mail")
        entry2 = integrations.set_enabled("calendar", False)
        assert entry2["enabled"] is False
        assert not integrations.enabled("calendar")

    def test_set_enabled_unknown_id(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setenv("AETHER_DATA_DIR", str(tmp_path))
        assert integrations.set_enabled("bogus", True) == {}

    def test_tolerates_missing_or_corrupt_state_file(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setenv("AETHER_DATA_DIR", str(tmp_path))
        assert integrations.catalog_status()  # missing file: no crash
        state_path = tmp_path / integrations.STATE_NAME
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("not json{{{")
        assert not integrations.enabled("calendar")  # corrupt file: no crash, defaults off
        statuses = integrations.catalog_status()
        assert all(e["enabled"] is False for e in statuses)


@pytest.mark.unit
class TestAppleIntegration:
    def test_split_records_roundtrip(self) -> None:
        usep, rsep = chr(31), chr(30)
        raw = f"a{usep}b{usep}c{rsep}d{usep}e{usep}f{rsep}"
        assert apple._split_records(raw) == [["a", "b", "c"], ["d", "e", "f"]]
        assert apple._split_records("") == []
        assert apple._split_records("\n\n") == []

    def test_notes_search_passes_query_and_limit_as_argv(self, monkeypatch) -> None:  # noqa: ANN001
        captured = {}

        def fake(source, args, timeout=10):  # noqa: ANN001, ANN202
            captured["source"] = source
            captured["args"] = args
            usep, rsep = chr(31), chr(30)
            raw = f"Groceries{usep}today{usep}milk, eggs{rsep}"
            return AppleScriptResult(0, raw, "")

        monkeypatch.setattr(apple, "run_applescript_args", fake)
        rows = apple.notes_search("milk", limit=5)
        assert captured["args"] == ["milk", "5"]
        # The query text is NEVER spliced into the script source.
        assert "milk" not in captured["source"]
        assert rows == [{"name": "Groceries", "modified": "today", "snippet": "milk, eggs"}]

    def test_notes_create_escapes_and_uses_br_for_newlines(self, monkeypatch) -> None:  # noqa: ANN001
        captured = {}

        def fake(source, args, timeout=10):  # noqa: ANN001, ANN202
            captured["args"] = args
            return AppleScriptResult(0, "OK", "")

        monkeypatch.setattr(apple, "run_applescript_args", fake)
        apple.notes_create("<Title>", "line1\nline2", folder="Work")
        body_html, folder = captured["args"]
        assert folder == "Work"
        assert "&lt;Title&gt;" in body_html  # escaped, not raw HTML injection
        assert "line1<br>line2" in body_html

    def test_mail_search_passes_mailbox_as_argv(self, monkeypatch) -> None:  # noqa: ANN001
        captured = {}

        def fake(source, args, timeout=10):  # noqa: ANN001, ANN202
            captured["args"] = args
            usep, rsep = chr(31), chr(30)
            raw = f"Re: Invoice{usep}sam@example.com{usep}today{usep}see attached{rsep}"
            return AppleScriptResult(0, raw, "")

        monkeypatch.setattr(apple, "run_applescript_args", fake)
        rows = apple.mail_search("invoice", limit=3, mailbox="Receipts")
        assert captured["args"] == ["invoice", "3", "Receipts"]
        assert rows == [{"subject": "Re: Invoice", "sender": "sam@example.com",
                         "date": "today", "snippet": "see attached"}]

    def test_automation_permission_error_named(self, monkeypatch) -> None:  # noqa: ANN001
        def fake(source, args, timeout=10):  # noqa: ANN001, ANN202
            return AppleScriptResult(1, "", "execution error: Not authorized. (-1743)")

        monkeypatch.setattr(apple, "run_applescript_args", fake)
        with pytest.raises(apple.AppleScriptError, match="Automation"):
            apple.notes_search("x")

    def test_timeout_error_named(self, monkeypatch) -> None:  # noqa: ANN001
        def fake(source, args, timeout=10):  # noqa: ANN001, ANN202
            return AppleScriptResult(124, "", "Timed out after 20s")

        monkeypatch.setattr(apple, "run_applescript_args", fake)
        with pytest.raises(apple.AppleScriptError, match="timed out"):
            apple.mail_search("x")

    def test_generic_failure_surfaces_stderr(self, monkeypatch) -> None:  # noqa: ANN001
        def fake(source, args, timeout=10):  # noqa: ANN001, ANN202
            return AppleScriptResult(1, "", "some other AppleScript error")

        monkeypatch.setattr(apple, "run_applescript_args", fake)
        with pytest.raises(apple.AppleScriptError, match="some other AppleScript error"):
            apple.notes_create("T", "b")
