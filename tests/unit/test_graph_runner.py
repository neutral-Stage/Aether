"""GraphRunner scheduling/gate/budget with fake fleet sessions (Phase 8).

No real agents or git — a FakeManager hands back sessions that 'complete'
immediately, so we test the orchestration logic deterministically.
"""
from __future__ import annotations

from aether.fleet import graph_runner
from aether.fleet.graph import TaskGraph, TaskNode
from aether.fleet.graph_runner import GraphRunner


class FakeSession:
    def __init__(self, sid, state="awaiting_input", result="ok"):
        self.session_id = sid
        self.state = state
        self.result_summary = result
        self.worktree_branch = None
        self.worktree_dir = None
        self.workspace = "/tmp/ws"

    def is_active(self):
        return self.state in ("starting", "running", "awaiting_input")


class FakeManager:
    """Spawns sessions that immediately land in a terminal-ish state."""
    def __init__(self, cost=0.0, capacity=99, fail_ids=None):
        self.sessions = {}
        self.spawn_order = []
        self.max_parallel_seen = 0
        self._active = 0
        self.capacity = capacity
        self.cost = cost
        self.fail_ids = fail_ids or set()
        self._counter = 0

    def spawn(self, *, agent_type, prompt, workspace, label, isolate):
        if self._active >= self.capacity:
            raise ValueError("fleet is full")
        self._counter += 1
        sid = f"s{self._counter}"
        state = "error" if label in self.fail_ids else "awaiting_input"
        s = FakeSession(sid, state=state)
        self.sessions[sid] = s
        self.spawn_order.append(label)
        return s

    def resolve(self, sid):
        return self.sessions.get(sid)

    def total_cost(self):
        return self.cost


def _n(nid, deps=None, paths=None, gate=None):
    return TaskNode(id=nid, title=nid, prompt=f"do {nid}", depends_on=deps or [],
                    paths=paths or [nid], gate_cmd=gate)


def _run(graph, manager, **kw):
    r = GraphRunner(manager, graph, "/tmp/ws", poll_sec=0.0, **kw)
    # no git repo at /tmp/ws → root is None, integration skipped cleanly
    return r.run()


def test_linear_chain_runs_in_order():
    g = TaskGraph("g", "goal", [_n("a"), _n("b", deps=["a"]), _n("c", deps=["b"])])
    m = FakeManager()
    _run(g, m)
    assert g.is_complete()
    assert [n.status for n in g.nodes.values()] == ["done", "done", "done"]
    assert m.spawn_order == ["a", "b", "c"]     # dependency order preserved


def test_independent_nodes_all_complete():
    g = TaskGraph("g", "goal", [_n("a", paths=["x"]), _n("b", paths=["y"]), _n("c", paths=["z"])])
    _run(g, FakeManager())
    assert all(n.status == "done" for n in g.nodes.values())


def test_failed_node_skips_dependents():
    g = TaskGraph("g", "goal", [_n("a"), _n("b", deps=["a"]), _n("c", deps=["b"])])
    _run(g, FakeManager(fail_ids={"a"}))
    assert g.nodes["a"].status == "failed"
    assert g.nodes["b"].status == "skipped"
    assert g.nodes["c"].status == "skipped"
    assert g.status == "failed"


def test_budget_ceiling_halts_scheduling():
    g = TaskGraph("g", "goal", [_n("a", paths=["x"]), _n("b", paths=["y"])])
    m = FakeManager(cost=10.0)
    r = GraphRunner(m, g, "/tmp/ws", poll_sec=0.0, budget_usd=5.0)
    r._schedule()
    assert m.spawn_order == []       # over budget: no work dispatched


def test_over_budget_run_terminates_no_hang():
    # regression: an over-budget graph must not spin forever — strand pending nodes
    g = TaskGraph("g", "goal", [_n("a", paths=["x"]), _n("b", paths=["y"])])
    _run(g, FakeManager(cost=10.0), budget_usd=5.0)
    assert g.is_complete()
    assert all(n.status == "skipped" for n in g.nodes.values())
    assert "budget" in g.nodes["a"].error


def test_errored_claude_turn_fails_node():
    # a turn that ends in awaiting_input but reported is_error must NOT be 'done'
    g = TaskGraph("g", "goal", [_n("a")])
    m = FakeManager()
    _run(g, m)  # baseline: fake completes ok
    assert g.nodes["a"].status == "done"

    g2 = TaskGraph("g", "goal", [_n("a")])
    m2 = FakeManager()
    orig_spawn = m2.spawn

    def spawn_erroring(**kw):
        s = orig_spawn(**kw)
        s.last_turn_error = True   # simulate is_error result while awaiting_input
        return s

    m2.spawn = spawn_erroring
    _run(g2, m2)
    assert g2.nodes["a"].status == "failed"


def test_cancel_strands_graph():
    # a cancelled graph (STOP) must terminate, stranding pending nodes
    g = TaskGraph("g", "goal", [_n("a"), _n("b", deps=["a"])])
    g.cancelled = True
    _run(g, FakeManager())
    assert g.is_complete()
    assert all(n.status == "skipped" for n in g.nodes.values())


def test_cancel_reaps_running_node_not_frozen():
    # a node already 'running' when STOP cancels must be finalized (reaped),
    # not left phantom-'running' and dropped from integration
    g = TaskGraph("g", "goal", [_n("a"), _n("b", deps=["a"])])
    m = FakeManager()
    # simulate 'a' already running with a session the STOP bridge just killed
    sess = m.spawn(agent_type="claude", prompt="x", workspace="/tmp",
                   label="a", isolate=True)
    g.nodes["a"].status = "running"
    g.nodes["a"].session_id = sess.session_id
    g.cancelled = True
    _run(g, m)
    assert g.is_complete()
    assert g.nodes["a"].is_terminal()          # reaped, not stuck 'running'
    assert g.nodes["b"].status == "skipped"


def test_manager_cancel_all_sets_flag():
    from aether.fleet.graph_runner import GraphManager
    GraphManager.reset()
    gm = GraphManager.get()
    g = gm.submit("goal", [_n("a", paths=["x"])], "/tmp/nope",
                  manager=FakeManager(), run_async=False)
    g.status = "running"  # simulate in-flight
    assert gm.cancel_all() == 1
    assert g.cancelled
    GraphManager.reset()


def test_gate_failure_marks_node_failed(monkeypatch):
    monkeypatch.setattr(graph_runner, "run_gate", lambda node, cwd: (False, "boom"))
    g = TaskGraph("g", "goal", [_n("a", gate="false")])
    _run(g, FakeManager())
    assert g.nodes["a"].status == "failed"
    assert "boom" in g.nodes["a"].error
