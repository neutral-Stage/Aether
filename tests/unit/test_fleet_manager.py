"""SessionManager tests with a dummy in-memory session (no subprocesses)."""
from __future__ import annotations

import time

import pytest

from aether.fleet.manager import SessionManager
from aether.fleet.session import AgentSession


class DummySession(AgentSession):
    def start(self) -> None:
        self._set_state("running")

    def send(self, text: str) -> bool:
        self._emit("text", f"got: {text}")
        return True


class FakeAudit:
    @staticmethod
    def get():
        return FakeAudit()

    def record(self, *args, **kwargs):
        return None


@pytest.fixture
def mgr(monkeypatch, tmp_path):
    SessionManager.reset()
    m = SessionManager.get()
    m.configure(
        {"max_sessions": 2, "worktrees": {"enabled": False}},
        approved_roots=[str(tmp_path)],
    )
    monkeypatch.setattr("aether.fleet.manager.AuditLog", FakeAudit)
    monkeypatch.setattr(
        m, "_build_session",
        lambda *, agent_type, **kw: DummySession(agent_type=agent_type, **kw),
    )
    yield m
    SessionManager.reset()


def test_spawn_and_list(mgr, tmp_path):
    s = mgr.spawn(agent_type="claude", prompt="task A", workspace=str(tmp_path))
    assert s.state == "running"
    listed = mgr.list()
    assert len(listed) == 1
    assert listed[0]["label"].startswith("claude-")


def test_max_sessions_enforced(mgr, tmp_path):
    mgr.spawn(agent_type="claude", prompt="a", workspace=str(tmp_path))
    mgr.spawn(agent_type="codex", prompt="b", workspace=str(tmp_path))
    with pytest.raises(ValueError, match="fleet is full"):
        mgr.spawn(agent_type="terminal", prompt="c", workspace=str(tmp_path))


def test_workspace_outside_roots_rejected(mgr):
    with pytest.raises(ValueError, match="outside approved roots"):
        mgr.spawn(agent_type="claude", prompt="x", workspace="/")


def test_unknown_agent_type(mgr, tmp_path):
    with pytest.raises(ValueError, match="unknown agent_type"):
        mgr.spawn(agent_type="skynet", prompt="x", workspace=str(tmp_path))


def test_resolve_by_label_and_prefix(mgr, tmp_path):
    s = mgr.spawn(
        agent_type="claude", prompt="x", workspace=str(tmp_path), label="fix-tests",
    )
    assert mgr.resolve("fix-tests") is s
    assert mgr.resolve(s.session_id[:4]) is s
    assert mgr.resolve("nope") is None


def test_send_and_stop(mgr, tmp_path):
    s = mgr.spawn(agent_type="claude", prompt="x", workspace=str(tmp_path))
    assert mgr.send(s.session_id, "more work") is True
    assert any("got: more work" in e.content for e in s.tail(10))
    mgr.stop(s.session_id)
    assert s.state == "stopped"
    with pytest.raises(ValueError):
        mgr.send("missing", "hi")


def test_summary_line_and_snapshot(mgr, tmp_path):
    assert mgr.summary_line() == ""
    s = mgr.spawn(
        agent_type="claude", prompt="x", workspace=str(tmp_path), label="alpha",
    )
    s._add_cost(0.05)
    line = mgr.summary_line()
    assert line.startswith("AGENT FLEET: 1 active")
    assert "alpha" in line
    snap = mgr.snapshot()
    assert snap["active_sessions"] == 1
    assert snap["spawned_total"] == 1
    assert snap["cost_usd_by_agent"]["claude"] == pytest.approx(0.05)


def test_stop_all(mgr, tmp_path):
    mgr.spawn(agent_type="claude", prompt="a", workspace=str(tmp_path))
    mgr.spawn(agent_type="codex", prompt="b", workspace=str(tmp_path))
    assert mgr.stop_all() == 2
    assert mgr.active_count() == 0


def test_watchdog_timeout(mgr, tmp_path):
    s = mgr.spawn(
        agent_type="claude", prompt="x", workspace=str(tmp_path), timeout_sec=1,
    )
    mgr._check_sessions(now=time.time() + 5)
    assert s.state == "timeout"


def test_watchdog_cost_cap(mgr, tmp_path):
    s = mgr.spawn(agent_type="claude", prompt="x", workspace=str(tmp_path))
    s.cost_cap_usd = 0.10
    s._add_cost(0.25)
    mgr._check_sessions()
    assert s.state == "stopped"
    assert any("cost cap" in e.content for e in s.tail(10))


def test_spawn_missing_binary_no_zombie(monkeypatch, tmp_path):
    """A start() that raises (missing CLI) must not leave a session stuck active."""
    SessionManager.reset()
    m = SessionManager.get()
    m.configure({"worktrees": {"enabled": False}}, approved_roots=[str(tmp_path)])
    monkeypatch.setattr("aether.fleet.manager.AuditLog", FakeAudit)

    class ExplodingSession(DummySession):
        def start(self) -> None:
            raise FileNotFoundError("codex not on PATH")

    monkeypatch.setattr(
        m, "_build_session",
        lambda *, agent_type, **kw: ExplodingSession(agent_type=agent_type, **kw),
    )
    s = m.spawn(agent_type="codex", prompt="x", workspace=str(tmp_path))
    assert s.state == "error"
    assert m.active_count() == 0  # does not count toward max_sessions
    assert any("failed to start" in e.content for e in s.tail(10))
    SessionManager.reset()


def test_event_sink_receives_fleet_events(mgr, tmp_path):
    events = []
    mgr.set_event_sink(events.append)
    s = mgr.spawn(agent_type="claude", prompt="x", workspace=str(tmp_path))
    s._emit("text", "hello")
    assert any(
        e["type"] == "fleet" and e["content"] == "hello" for e in events
    )
