"""Fleet HTTP API lifecycle tests (FastAPI TestClient, dummy sessions)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aether.fleet.manager import SessionManager
from aether.fleet.session import AgentSession
from sidecar.server import app


class DummySession(AgentSession):
    def start(self) -> None:
        self._set_state("running")
        self._emit("text", "dummy started")

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
def client(monkeypatch, tmp_path):
    SessionManager.reset()
    mgr = SessionManager.get()
    mgr.configure(
        {"worktrees": {"enabled": False}}, approved_roots=[str(tmp_path)],
    )
    monkeypatch.setattr("aether.fleet.manager.AuditLog", FakeAudit)
    monkeypatch.setattr(
        mgr, "_build_session",
        lambda *, agent_type, **kw: DummySession(agent_type=agent_type, **kw),
    )
    from sidecar.rate_limit import get_limiter
    get_limiter().reset("fleet_spawn")
    with TestClient(app) as c:
        yield c, str(tmp_path)
    SessionManager.reset()


def test_fleet_lifecycle(client):
    c, workspace = client
    r = c.get("/fleet")
    assert r.status_code == 200
    assert r.json()["sessions"] == []

    r = c.post("/fleet/spawn", json={
        "agent_type": "claude", "prompt": "fix tests", "workspace": workspace,
    })
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    assert r.json()["state"] == "running"

    r = c.get(f"/fleet/{sid}")
    assert r.status_code == 200

    r = c.post(f"/fleet/{sid}/send", json={"text": "carry on"})
    assert r.status_code == 200
    assert r.json()["delivered"] is True

    r = c.get(f"/fleet/{sid}/output", params={"since_seq": 0})
    assert r.status_code == 200
    contents = [e["content"] for e in r.json()["events"]]
    assert any("carry on" in x for x in contents)

    r = c.post(f"/fleet/{sid}/stop")
    assert r.status_code == 200
    assert r.json()["state"] == "stopped"


def test_spawn_validation_and_404(client):
    c, workspace = client
    r = c.post("/fleet/spawn", json={
        "agent_type": "skynet", "prompt": "x", "workspace": workspace,
    })
    assert r.status_code == 400

    r = c.post("/fleet/spawn", json={
        "agent_type": "claude", "prompt": "x", "workspace": "/",
    })
    assert r.status_code == 400

    assert c.get("/fleet/nope").status_code == 404


def test_max_sessions_rejection(client):
    c, workspace = client
    SessionManager.get().configure(
        {"max_sessions": 1, "worktrees": {"enabled": False}},
        approved_roots=[workspace],
    )
    r = c.post("/fleet/spawn", json={
        "agent_type": "claude", "prompt": "a", "workspace": workspace,
    })
    assert r.status_code == 200
    r = c.post("/fleet/spawn", json={
        "agent_type": "codex", "prompt": "b", "workspace": workspace,
    })
    assert r.status_code == 400
    assert "fleet is full" in r.json()["error"]["message"]


def test_stop_all(client):
    c, workspace = client
    for agent in ("claude", "codex"):
        c.post("/fleet/spawn", json={
            "agent_type": agent, "prompt": "x", "workspace": workspace,
        })
    r = c.post("/fleet/stop_all")
    assert r.status_code == 200
    assert r.json()["stopped"] == 2
