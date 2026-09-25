"""Fleet warm pool: spare Claude Code processes, adopted by the next session in a folder."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from aether.fleet import manager as manager_mod
from aether.fleet import session as session_mod
from aether.fleet import worktree as wt
from aether.fleet.manager import SessionManager
from aether.fleet.warm_pool import Warm, WarmPool

ECHO = "import sys\nfor line in sys.stdin:\n    sys.stdout.write(line)\n    sys.stdout.flush()\n"


class FakeProc:
    def __init__(self, alive: bool = True) -> None:
        self.alive = alive
        self.terminated = False

    def poll(self):  # noqa: ANN201
        return None if self.alive else 0

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False

    kill = terminate

    def wait(self, timeout=None) -> int:  # noqa: ANN001
        return 0


def _warm(key: str, created: float, **kw) -> Warm:  # noqa: ANN003
    base = {"key": (key,), "session_id": key, "run_dir": "/tmp", "branch": None, "head": "",
            "proc": FakeProc(), "workspace": "/tmp", "created_at": created}
    return Warm(**{**base, **kw})


def test_take_put_expire_and_evict() -> None:
    now = [1000.0]
    pool = WarmPool(max_total=2, max_age_sec=60, clock=lambda: now[0])
    a, b, c = _warm("a", 1000), _warm("b", 1001), _warm("c", 1002)
    for w in (a, b, c):
        pool.put(w)
    assert a.proc.terminated and sorted(x["session_id"] for x in pool.status()) == ["b", "c"]
    assert pool.take(("b",)) is b and pool.adopted == 1
    assert pool.take(("b",)) is None
    now[0] = 1100.0
    assert pool.expire() == 1 and c.proc.terminated
    dead = _warm("d", 1100, proc=FakeProc(alive=False))
    pool.put(dead)
    assert pool.take(("d",)) is None
    pool.put(_warm("e", 1100))
    assert pool.drain() == 1 and pool.status() == []


def test_stale_worktree_is_not_used(monkeypatch) -> None:  # noqa: ANN001
    discarded = []
    monkeypatch.setattr(WarmPool, "_discard", staticmethod(discarded.append))
    pool = WarmPool(clock=lambda: 0.0)
    plain = _warm("k", 0.0, head="abc")
    pool.put(plain)
    assert pool.take(("k",), head="def") is plain       # not isolated: HEAD doesn't matter
    iso = _warm("i", 0.0, branch="aether/i", head="abc")
    pool.put(iso)
    assert pool.take(("i",), head="def") is None and discarded == [iso]
    pool.put(iso)
    assert pool.take(("i",), head="abc") is iso


def test_fill_async_once_per_key_and_disabled() -> None:
    pool = WarmPool(max_total=1, clock=lambda: 5.0)
    calls = []

    def factory():  # noqa: ANN202
        calls.append(1)
        time.sleep(0.05)
        return _warm("k", 0.0)

    assert pool.fill_async(("k",), factory)
    assert not pool.fill_async(("k",), factory)
    pool.wait_idle()
    assert calls == [1] and pool.status()[0]["session_id"] == "k"
    assert not pool.fill_async(("k",), factory)          # already has one
    pool.max_total = 0
    assert not pool.fill_async(("z",), factory)
    pool.fill_async(("x",), lambda: 1 / 0)                # failures only cost latency
    pool.wait_idle()


# ---- the manager ---------------------------------------------------------------------------

@pytest.fixture
def fleet(monkeypatch, tmp_path):  # noqa: ANN001, ANN201
    started: list[tuple[list[str], str]] = []

    def fake_start(cmd, workspace, env_allowlist, *, stdin_pipe=False):  # noqa: ANN001, ANN202
        started.append((list(cmd), str(workspace)))
        return subprocess.Popen([sys.executable, "-u", "-c", ECHO], cwd=workspace,  # noqa: S603
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, bufsize=1)

    monkeypatch.setattr(session_mod, "start_process", fake_start)
    monkeypatch.setattr(manager_mod, "start_process", fake_start)

    class Audit:
        records: list = []

        @staticmethod
        def get():  # noqa: ANN205
            return Audit()

        def record(self, *args, **kw) -> None:  # noqa: ANN002, ANN003
            Audit.records.append((args, kw))

    monkeypatch.setattr(manager_mod, "AuditLog", Audit)
    SessionManager.reset()
    m = SessionManager.get()
    m.started = started
    m.audit = Audit.records
    yield m
    m.stop_all(grace_sec=0.2)
    SessionManager.reset()


def _wait_for(session, text: str, timeout: float = 5.0) -> bool:  # noqa: ANN001
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(text in e.content for e in session.tail(50)):
            return True
        time.sleep(0.02)
    return False


def test_next_session_adopts_the_spare(fleet, tmp_path) -> None:  # noqa: ANN001
    fleet.configure({"worktrees": {"enabled": False}}, approved_roots=[str(tmp_path)])
    first = fleet.spawn(agent_type="claude", prompt="task A", workspace=str(tmp_path))
    fleet._pool.wait_idle()  # noqa: SLF001
    assert len(fleet.started) == 2 and fleet.started[1][0][:2] == ["claude", "-p"]
    spare = fleet.warm_status()
    assert len(spare) == 1 and spare[0]["workspace"] == str(tmp_path.resolve())
    second = fleet.spawn(agent_type="claude", prompt="task B", workspace=str(tmp_path))
    assert second.session_id == spare[0]["session_id"] != first.session_id
    assert _wait_for(second, "task B") and _wait_for(first, "task A")
    assert fleet._pool.adopted == 1  # noqa: SLF001
    assert fleet.audit[-1][1]["extra"]["warm"] is True
    fleet._pool.wait_idle()  # noqa: SLF001
    assert len(fleet.started) == 3          # a new spare replaced the used one
    fleet.stop_all(grace_sec=0.2)
    assert fleet.warm_status() == []


def test_settings_change_means_no_match(fleet, tmp_path) -> None:  # noqa: ANN001
    fleet.configure({"worktrees": {"enabled": False}}, approved_roots=[str(tmp_path)])
    fleet.spawn(agent_type="claude", prompt="a", workspace=str(tmp_path))
    fleet._pool.wait_idle()  # noqa: SLF001
    fleet.configure({"worktrees": {"enabled": False}, "claude_permission_mode": "plan"})
    s = fleet.spawn(agent_type="claude", prompt="b", workspace=str(tmp_path))
    assert fleet._pool.adopted == 0 and "--permission-mode" in fleet.started[-2][0]  # noqa: SLF001
    assert s.state in ("running", "awaiting_input")


def test_disabled_pool_and_other_agents_start_nothing_extra(fleet, tmp_path) -> None:  # noqa: ANN001
    fleet.configure({"worktrees": {"enabled": False}, "warm_pool": {"enabled": False}},
                    approved_roots=[str(tmp_path)])
    fleet.spawn(agent_type="claude", prompt="a", workspace=str(tmp_path))
    fleet._pool.wait_idle()  # noqa: SLF001
    assert len(fleet.started) == 1 and fleet.warm_status() == []


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,  # noqa: S603
                          text=True).stdout.strip()


def test_isolated_spare_is_dropped_when_head_moves(fleet, tmp_path) -> None:  # noqa: ANN001
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("1")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "one")
    fleet.configure({"worktrees": {"enabled": True}}, approved_roots=[str(tmp_path)])
    fleet.spawn(agent_type="claude", prompt="a", workspace=str(repo))
    fleet._pool.wait_idle()  # noqa: SLF001
    spare = fleet.warm_status()[0]
    assert spare["isolated"] and f"aether/{spare['session_id']}" in _git(repo, "branch")
    (repo / "a.txt").write_text("2")
    _git(repo, "commit", "-qam", "two")
    s = fleet.spawn(agent_type="claude", prompt="b", workspace=str(repo))
    assert s.session_id != spare["session_id"] and fleet._pool.adopted == 0  # noqa: SLF001
    assert f"aether/{spare['session_id']}" not in _git(repo, "branch")
    assert not (repo / ".aether-worktrees" / spare["session_id"]).exists()
    assert wt.head_commit(repo) == _git(repo, "rev-parse", "HEAD")
