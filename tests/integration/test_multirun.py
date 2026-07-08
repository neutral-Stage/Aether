"""Multi-run sidecar: run registry, /runs endpoints, concurrency gate, /stop."""
from __future__ import annotations

import pytest

from sidecar import server
from sidecar.server import RunState, _run_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    _run_registry._runs.clear()
    _run_registry._order.clear()
    yield
    _run_registry._runs.clear()
    _run_registry._order.clear()


def _mk(run_id: str, status: str = "running") -> RunState:
    st = RunState(run_id=run_id, goal=f"goal-{run_id}")
    st.status = status
    return st


def test_registry_latest_and_active():
    _run_registry.register(_mk("a", "idle"))
    _run_registry.register(_mk("b", "running"))
    assert _run_registry.latest().run_id == "b"
    assert [s.run_id for s in _run_registry.active()] == ["b"]


def test_registry_prunes_finished_only():
    for i in range(_run_registry.history + 5):
        _run_registry.register(_mk(f"done-{i}", "idle"))
    active = _mk("live", "running")
    _run_registry.register(active)
    # active run is never pruned even past the history cap
    assert _run_registry.get("live") is not None
    assert len(_run_registry.all()) <= _run_registry.history + 1


def test_runs_endpoint_lists(sidecar_client):
    _run_registry.register(_mk("r1", "idle"))
    _run_registry.register(_mk("r2", "running"))
    data = sidecar_client.get("/runs").json()
    ids = {r["id"] for r in data["runs"]}
    assert {"r1", "r2"} <= ids
    assert data["active"] == 1


def test_run_detail_404(sidecar_client):
    assert sidecar_client.get("/runs/nope").status_code == 404


def test_run_detail_ok(sidecar_client):
    _run_registry.register(_mk("solo", "idle"))
    data = sidecar_client.get("/runs/solo").json()
    assert data["id"] == "solo" and data["status"] == "idle"


def test_status_reports_concurrency(sidecar_client):
    _run_registry.register(_mk("s1", "running"))
    data = sidecar_client.get("/status").json()
    assert data["status"] == "running"
    assert data["active_runs"] == 1
    assert data["max_concurrent_runs"] >= 1


def test_run_gate_409_when_cap_reached(monkeypatch, sidecar_client):
    monkeypatch.setattr(server, "_max_concurrent_runs", lambda: 1)
    _run_registry.register(_mk("busy", "running"))
    resp = sidecar_client.post("/run", json={"goal": "do a thing"})
    assert resp.status_code == 409


def test_run_gate_allows_under_cap(monkeypatch, sidecar_client):
    # cap 2, one active → gate passes (goal validation still applies)
    monkeypatch.setattr(server, "_max_concurrent_runs", lambda: 2)
    _run_registry.register(_mk("busy", "running"))
    # empty goal → 400 proves we passed the 409 gate
    resp = sidecar_client.post("/run", json={"goal": "   "})
    assert resp.status_code == 400


def test_stop_marks_active_and_is_global(monkeypatch, sidecar_client):
    from aether.core import stop as stop_ctl
    triggered = {}
    monkeypatch.setattr(stop_ctl, "trigger", lambda reason="": triggered.update(hit=True))
    _run_registry.register(_mk("x", "running"))
    _run_registry.register(_mk("y", "running"))
    data = sidecar_client.post("/stop", json={}).json()
    assert triggered.get("hit")  # global panic button fired
    assert set(data["run_ids"]) == {"x", "y"}
    assert _run_registry.get("x").status == "stopped"


def test_run_async_preserves_stop_when_not_resetting():
    """reset_stop=False must NOT clear a pending STOP (sibling-run safety)."""
    import asyncio
    from aether.core import stop as stop_ctl
    from aether.core.config import load_config
    from aether.core.orchestrator import Agent

    stop_ctl.trigger("test")  # simulate a sibling's pending STOP
    assert stop_ctl.is_set()
    try:
        agent = Agent(load_config(validate=False))

        async def _drive():
            # STOP is already set → the loop should bail immediately; the point
            # is that entering the run with reset_stop=False leaves it SET.
            return await agent.run_async("noop goal", run_id="rc",
                                         reset_stop=False)

        asyncio.run(_drive())
        assert stop_ctl.is_set(), "reset_stop=False wrongly cleared the STOP flag"
    finally:
        stop_ctl.reset()


def test_run_async_resets_stop_by_default():
    import asyncio
    import contextlib
    from aether.core import stop as stop_ctl
    from aether.core.config import load_config
    from aether.core.orchestrator import Agent

    stop_ctl.trigger("test")
    try:
        agent = Agent(load_config(validate=False))
        # reset happens on the first line of the run; the no-LLM RuntimeError
        # fires only afterwards, so suppress it and check STOP was cleared.
        with contextlib.suppress(RuntimeError):
            asyncio.run(agent.run_async("noop", run_id="rd", reset_stop=True))
        assert not stop_ctl.is_set(), "default run should have cleared STOP"
    finally:
        stop_ctl.reset()


def test_stop_specific_run_id(monkeypatch, sidecar_client):
    from aether.core import stop as stop_ctl
    monkeypatch.setattr(stop_ctl, "trigger", lambda reason="": None)
    _run_registry.register(_mk("keep", "running"))
    _run_registry.register(_mk("kill", "running"))
    data = sidecar_client.post("/stop", json={"run_id": "kill"}).json()
    assert data["run_ids"] == ["kill"]
    assert _run_registry.get("kill").status == "stopped"
    assert _run_registry.get("keep").status == "running"  # rows narrowed by run_id
