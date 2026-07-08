"""spawn_graph/get_graph tools + /fleet/graphs endpoint (Phase 8)."""
from __future__ import annotations

import pytest

from aether.fleet.graph import TaskNode
from aether.fleet.graph_runner import GraphManager
from aether.fleet.tools import _h_get_graph, _h_spawn_graph


class _FakeSession:
    def __init__(self, sid):
        self.session_id = sid
        self.state = "awaiting_input"
        self.result_summary = "ok"
        self.worktree_branch = None
        self.worktree_dir = None
        self.workspace = "/tmp/x"

    def is_active(self):
        return False


class _FakeManager:
    def __init__(self):
        self._n = 0
        self.sessions = {}

    def spawn(self, **kw):
        self._n += 1
        s = _FakeSession(f"s{self._n}")
        self.sessions[s.session_id] = s
        return s

    def resolve(self, sid):
        return self.sessions.get(sid)

    def total_cost(self):
        return 0.0


def test_spawn_graph_rejects_empty_nodes():
    assert "ERROR" in _h_spawn_graph({"nodes": []}, None)


def test_spawn_graph_rejects_bad_node():
    # missing required 'prompt'
    assert "ERROR" in _h_spawn_graph({"nodes": [{"id": "a"}]}, None)


def test_spawn_graph_rejects_terminal_node():
    out = _h_spawn_graph(
        {"nodes": [{"id": "a", "prompt": "x", "agent_type": "terminal"}]}, None)
    assert "ERROR" in out and "terminal" in out


def test_get_graph_formats_and_endpoint(sidecar_client):
    GraphManager.reset()
    gm = GraphManager.get()
    # run synchronously with a fake fleet + a non-repo workspace (integration skipped)
    graph = gm.submit(
        goal="ship the thing",
        nodes=[
            TaskNode(id="a", title="build", prompt="x", paths=["a"]),
            TaskNode(id="b", title="test", prompt="y", depends_on=["a"], paths=["b"]),
        ],
        workspace="/tmp/aether-no-repo",
        manager=_FakeManager(),
        run_async=False,
    )
    assert graph.is_complete()

    # tool formatting
    out = _h_get_graph({"graph_id": graph.graph_id}, None)
    assert "build" in out and "test" in out

    # endpoint list + detail
    listing = sidecar_client.get("/fleet/graphs").json()
    assert any(g["graph_id"] == graph.graph_id for g in listing["graphs"])
    detail = sidecar_client.get(f"/fleet/graphs/{graph.graph_id}").json()
    assert detail["status"] in ("done", "failed")
    assert len(detail["nodes"]) == 2
    assert sidecar_client.get("/fleet/graphs/nope").status_code == 404
    GraphManager.reset()


def test_graph_tools_registered():
    from aether.tools.registry import build_default_registry
    names = set(build_default_registry()._tools)
    assert {"spawn_graph", "get_graph"} <= names


@pytest.fixture(autouse=True)
def _reset_graph_manager():
    yield
    GraphManager.reset()
