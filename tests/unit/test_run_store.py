"""Durable state store + startup reconcile (Phase 9)."""
from __future__ import annotations

import pytest

from sidecar import run_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(run_store, "_PATH", tmp_path / "events.jsonl")
    return run_store


def test_append_replay_last_wins(store):
    store.append("run", "r1", "running", goal="g")
    store.append("run", "r1", "idle")
    store.append("graph", "g1", "running")
    folded = store.replay()
    assert folded["run:r1"]["status"] == "idle"        # last line wins
    assert folded["graph:g1"]["status"] == "running"


def test_non_terminal_filters_finished(store):
    store.append("run", "done1", "running")
    store.append("run", "done1", "idle")               # terminal
    store.append("graph", "live1", "running")          # non-terminal
    nt = {e["id"] for e in store.non_terminal()}
    assert "live1" in nt and "done1" not in nt


def test_reconcile_marks_running_as_interrupted(store):
    from sidecar import server
    server._run_registry._runs.clear()
    server._run_registry._order.clear()
    store.append("run", "rX", "running", goal="do X")

    server._reconcile_persisted_state()

    st = server._run_registry.get("rX")
    assert st is not None
    assert st.status == "interrupted"
    # a fresh 'interrupted' event was persisted → no longer a reconcile target
    assert not any(e["id"] == "rX" for e in store.non_terminal())
    server._run_registry._runs.clear()
    server._run_registry._order.clear()


def test_append_never_raises_on_bad_dir(monkeypatch, tmp_path):
    # parent path is a FILE → mkdir raises NotADirectoryError (OSError); durability
    # is best-effort and must never propagate into the agent loop.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    monkeypatch.setattr(run_store, "_PATH", blocker / "sub" / "e.jsonl")
    run_store.append("run", "r", "running")  # swallowed, no raise
