"""Live VM benchmark: task file health, checks, and the Lume/SSH harness (faked)."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace as NS

import pytest

from aether.core.focus import FocusState
from aether.core.policy import Policy, PolicyConfig
from aether.tools.registry import DEFAULT_REGISTRY
from tests.benchmark import scorer
from tests.benchmark import vm_runner as vr

TASKS = vr.load_live_tasks()


def _cp(out: str = "", code: int = 0, err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, out, err)


# ---- task file ------------------------------------------------------------------------------

def test_enough_unique_tasks() -> None:
    ids = [t["id"] for t in TASKS]
    assert len(ids) >= 30 and len(ids) == len(set(ids))


def _check_kinds(check: dict) -> list[str]:
    kinds = [k for k in check if k in vr.CHECK_TYPES]
    assert len(kinds) == 1, check
    if kinds[0] == "all":
        return [k for c in check["all"] for k in _check_kinds(c)]
    return kinds


@pytest.mark.parametrize("task", TASKS, ids=[t["id"] for t in TASKS])
def test_task_is_well_formed(task: dict) -> None:
    assert task["goal"].strip() and task["check"]
    _check_kinds(task["check"])
    for key in ("setup", "cleanup"):
        assert all(isinstance(c, str) and c.strip() for c in task.get(key) or [])
    result = scorer.score_trace(task, task["mock_trace"])
    assert result.passed, result.reason


@pytest.mark.parametrize("task", TASKS, ids=[t["id"] for t in TASKS])
def test_mock_traces_use_real_tools_and_need_no_confirmation(task: dict) -> None:
    """Unattended runs cannot answer a confirmation, so no step may need one."""
    policy = Policy(PolicyConfig())
    for step in task["mock_trace"]:
        spec = DEFAULT_REGISTRY.get(step["tool"])
        assert spec is not None, step["tool"]
        assert not policy.requires_confirm(spec, step["args"], FocusState()), step


# ---- checks ---------------------------------------------------------------------------------

def test_evaluate_check_types() -> None:
    sent: list[str] = []

    def ssh(cmd: str) -> subprocess.CompletedProcess:
        sent.append(cmd)
        if cmd.startswith("osascript"):
            return _cp("https://example.com/\n")
        if cmd.startswith("test -e"):
            return _cp("present") if "yes" in cmd else _cp("", 1)
        if cmd.startswith("cat"):
            return _cp("buy milk\n")
        return _cp("ok")

    assert vr.evaluate({"applescript": "get url", "expect": "example.com"}, ssh).passed
    assert not vr.evaluate({"applescript": "get url", "expect": "apple.com"}, ssh).passed
    assert vr.evaluate({"file_exists": "~/Desktop/yes.txt"}, ssh).passed
    assert not vr.evaluate({"file_exists": "~/Desktop/no.txt"}, ssh).passed
    assert vr.evaluate({"file_contains": {"path": "/tmp/x", "text": "milk"}}, ssh).passed
    assert vr.evaluate({"answer_contains": "b12"}, ssh, answer="Level 3, B12").passed
    assert vr.evaluate({"answer_regex": r"\b(3|three)\b"}, ssh, answer="There are three.").passed
    assert not vr.evaluate({"answer_regex": r"\b3\b"}, ssh, answer="30 files").passed
    combo = vr.evaluate({"all": [{"file_exists": "~/yes"}, {"file_exists": "~/no"}]}, ssh)
    assert not combo.passed and "file_exists" in combo.detail
    assert not vr.evaluate({"mystery": 1}, ssh).passed
    # ~ stays expandable, the rest is quoted
    assert any(c.startswith('test -e "$HOME"/Desktop/yes.txt') for c in sent)
    assert vr._path("~/My Files/a b.txt") == '"$HOME"/\'My Files/a b.txt\''  # noqa: SLF001


def test_evaluate_frontmost_and_failed_command() -> None:
    assert vr.evaluate({"frontmost": "Safari"}, lambda c: _cp("Safari")).passed
    assert not vr.evaluate({"shell": "x", "expect": "ok"}, lambda c: _cp("ok", 1)).passed


def test_osascript_lines_become_separate_e_args() -> None:
    cmd = vr._osascript('tell application "Calendar"\n  return 1\nend tell\n')  # noqa: SLF001
    assert cmd.count(" -e ") == 3


@pytest.mark.parametrize(("text", "ip"), [
    ('{"name": "v", "ipAddress": "192.168.64.5"}', "192.168.64.5"),
    ('[{"name": "v", "ip_address": "10.0.0.2"}]', "10.0.0.2"),
    ("name v status running ip 192.168.64.9", "192.168.64.9"),
    ('{"name": "v"}', ""), ("", ""),
])
def test_parse_ip(text: str, ip: str) -> None:
    assert vr.parse_ip(text) == ip


# ---- the VM wrapper -----------------------------------------------------------------------

CONFIG = vr.load_vm_config()


def _fake_vm(ssh_ok: bool = True, ip_json: str = '{"ipAddress": "192.168.64.5"}'):  # noqa: ANN202
    calls: list[list[str]] = []

    def run(argv, **kw):  # noqa: ANN001, ANN003, ANN202
        calls.append(list(argv))
        if argv[:2] == ["lume", "get"]:
            return _cp(ip_json)
        if argv[0] == "ssh":
            return _cp("ok", 0 if ssh_ok else 255)
        return _cp()

    vm = vr.LumeVM(CONFIG, "bench-1", run=run, popen=lambda argv, **kw: NS(terminate=lambda: None),
                   sleep=lambda s: None)
    return vm, calls


def test_vm_lifecycle_commands() -> None:
    vm, calls = _fake_vm()
    vm.create()
    vm.start()
    assert vm.wait_ready(timeout=5) == "192.168.64.5"
    vm.destroy()
    lume = [c for c in calls if c[0] == "lume"]
    assert lume[0] == ["lume", "clone", "aether-golden", "bench-1"]
    assert ["lume", "stop", "bench-1"] in lume and ["lume", "delete", "bench-1", "--force"] in lume
    ssh = next(c for c in calls if c[0] == "ssh")
    assert ssh[-2] == "lume@192.168.64.5" and ssh[-1] == "true"
    assert "-i" in ssh


def test_vm_wait_times_out(monkeypatch) -> None:  # noqa: ANN001
    vm, _ = _fake_vm(ssh_ok=False)
    clock = iter(range(0, 1000, 50))
    monkeypatch.setattr(vr.time, "monotonic", lambda: float(next(clock)))
    with pytest.raises(TimeoutError):
        vm.wait_ready(timeout=100)


def test_run_goal_posts_through_ssh() -> None:
    seen: list[str] = []

    def ssh(cmd, timeout=0):  # noqa: ANN001, ANN202
        seen.append(cmd)
        return _cp('{"status": "idle", "result": "done"}')

    body = vr.run_goal(ssh, "open 'safari'", CONFIG)
    assert body["result"] == "done"
    assert "Authorization: Bearer aether-bench" in seen[0] and "/run" in seen[0]
    garbled = vr.run_goal(lambda c, timeout=0: _cp("<html>"), "x", CONFIG)
    assert garbled["status"] == "error"


class FakeVM:
    instances: list = []

    def __init__(self, name: str, answers: dict | None = None, boom: bool = False) -> None:
        self.name, self.cmds, self.destroyed, self.boom = name, [], False, boom
        self.answers = answers or {}
        FakeVM.instances.append(self)

    def create(self) -> None:
        if self.boom:
            raise RuntimeError("clone failed")

    def start(self) -> None: ...
    def wait_ready(self, timeout: float = 0) -> str:
        return "1.2.3.4"

    def sleep(self, s: float) -> None: ...

    def ssh(self, cmd: str, timeout: float = 0) -> subprocess.CompletedProcess:
        self.cmds.append(cmd)
        if "/run" in cmd:
            return _cp('{"status": "idle", "result": "It is Example Domain."}')
        if cmd.startswith("fail-setup"):
            return _cp("", 1, "nope")
        return _cp("present")

    def destroy(self) -> None:
        self.destroyed = True


TASK_OK = {"id": "a", "goal": "title?", "check": {"answer_contains": "Example Domain"},
           "setup": ["echo hi"], "cleanup": ["echo bye"]}
TASK_BAD_SETUP = {"id": "b", "goal": "x", "check": {"file_exists": "/x"}, "setup": ["fail-setup"]}


def test_run_suite_clones_per_task_and_scores_by_state() -> None:
    FakeVM.instances = []
    results = vr.run_suite([TASK_OK, TASK_BAD_SETUP], {}, vm_factory=FakeVM, log=lambda s: None)
    assert [r.passed for r in results] == [True, False]
    assert "setup failed" in results[1].reason
    assert len(FakeVM.instances) == 2 and all(v.destroyed for v in FakeVM.instances)
    assert FakeVM.instances[0].cmds[0] == "echo hi" and FakeVM.instances[0].cmds[-1] == "echo bye"


def test_run_suite_reuse_keep_and_harness_errors() -> None:
    FakeVM.instances = []
    vr.run_suite([TASK_OK, TASK_OK], {}, reuse_vm=True, vm_factory=FakeVM, log=lambda s: None)
    assert len(FakeVM.instances) == 1 and FakeVM.instances[0].destroyed
    FakeVM.instances = []
    vr.run_suite([TASK_OK], {}, keep=True, vm_factory=FakeVM, log=lambda s: None)
    assert not FakeVM.instances[0].destroyed
    broken = vr.run_suite([TASK_OK], {}, vm_factory=lambda n: FakeVM(n, boom=True),
                          log=lambda s: None)
    assert not broken[0].passed and "harness error: clone failed" in broken[0].reason


def test_select_and_summarize() -> None:
    tasks = [{"id": "a"}, {"id": "m", "requires": ["mail_account"]}, {"id": "c"}]
    run, skipped = vr.select_tasks(tasks)
    assert [t["id"] for t in run] == ["a", "c"] and [t["id"] for t in skipped] == ["m"]
    run, _ = vr.select_tasks(tasks, only=["m"], have=["mail_account"])
    assert [t["id"] for t in run] == ["m"]
    results = [vr.LiveResult("a", True, ""), vr.LiveResult("b", True, ""),
               vr.LiveResult("c", False, "")]
    summary = vr.summarize_live(results, skipped)
    assert summary["pass_rate_pct"] == 66.7 and summary["meets_bar"]
    assert summary["skipped"] == ["m"]
    assert not vr.summarize_live([vr.LiveResult("a", False, "")])["meets_bar"]
