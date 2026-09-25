"""Live benchmark in throwaway macOS VMs (Lume), scored by real end state.

Each task runs in a fresh clone of a "golden" VM that already has Aether
installed, its permissions granted and its checks pre-approved (see
docs/BENCHMARK_VM.md). The runner:

1. clones the golden VM and boots it headless (``lume clone`` / ``lume run``),
2. waits for SSH, runs the task's ``setup`` commands,
3. sends the goal to the sidecar inside the VM (POST /run over SSH),
4. scores the task by the machine's real state (``check``): AppleScript
   queries, files, the frontmost app, or the agent's answer, never by the
   agent's own claim of success,
5. runs ``cleanup`` and deletes the clone.

Everything that touches the outside world goes through two injectable
callables (``run`` for local commands, ``ssh`` for commands in the VM), so
the logic is unit-tested without a VM. Lume command lines live in
scripts/vm/lume.yaml, not here: the CLI changes faster than this file.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
LIVE_TASKS_PATH = ROOT / "tests" / "benchmark" / "live_tasks.yaml"
VM_CONFIG_PATH = ROOT / "scripts" / "vm" / "lume.yaml"
PASS_BAR_PCT = 60.0

CHECK_TYPES = frozenset({"applescript", "shell", "frontmost", "file_exists", "file_missing",
                         "file_contains", "answer_contains", "answer_regex", "all"})

Runner = Callable[..., subprocess.CompletedProcess]


def load_live_tasks(path: Path | None = None) -> list[dict[str, Any]]:
    data = yaml.safe_load((path or LIVE_TASKS_PATH).read_text()) or {}
    return list(data.get("tasks") or [])


def load_vm_config(path: Path | None = None) -> dict[str, Any]:
    return dict(yaml.safe_load((path or VM_CONFIG_PATH).read_text()) or {})


def _fmt(template: str, **values: str) -> list[str]:
    """A command template from lume.yaml → argv, with values quoted safely."""
    return shlex.split(template.format(**{k: shlex.quote(str(v)) for k, v in values.items()}))


# ---- the VM -------------------------------------------------------------------------------

@dataclass
class LumeVM:
    """One clone of the golden image, driven through the lume CLI and SSH."""

    config: dict[str, Any]
    name: str
    run: Runner = subprocess.run
    popen: Callable[..., Any] = subprocess.Popen
    sleep: Callable[[float], None] = time.sleep
    ip: str = ""
    _proc: Any = None

    def _lume(self, key: str, **values: str) -> subprocess.CompletedProcess:
        argv = _fmt(self.config["commands"][key], name=self.name,
                    golden=self.config.get("golden", "aether-golden"), **values)
        return self.run(argv, capture_output=True, text=True, timeout=600)

    def create(self) -> None:
        res = self._lume("clone")
        if res.returncode != 0:
            raise RuntimeError(f"lume clone failed: {res.stderr.strip()[:300]}")

    def start(self) -> None:
        argv = _fmt(self.config["commands"]["run"], name=self.name,
                    golden=self.config.get("golden", "aether-golden"))
        self._proc = self.popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait_ready(self, timeout: float = 240.0) -> str:
        """Poll for an IP address, then for SSH to answer."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.ip:
                self.ip = parse_ip(self._lume("get").stdout)
            if self.ip and self.ssh("true", timeout=10).returncode == 0:
                return self.ip
            self.sleep(3.0)
        raise TimeoutError(f"VM {self.name} did not come up within {timeout:.0f}s")

    def ssh(self, command: str, timeout: float = 120.0) -> subprocess.CompletedProcess:
        ssh_cfg = self.config.get("ssh") or {}
        argv = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                "-o", "ConnectTimeout=5", "-o", "LogLevel=ERROR"]
        if ssh_cfg.get("key"):
            argv += ["-i", str(Path(str(ssh_cfg["key"])).expanduser())]
        argv += [f"{ssh_cfg.get('user', 'lume')}@{self.ip}", command]
        return self.run(argv, capture_output=True, text=True, timeout=timeout)

    def destroy(self) -> None:
        self._lume("stop")
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        self._lume("delete")


def parse_ip(text: str) -> str:
    """The VM's IP from `lume get -f json` (object or list), or ''."""
    try:
        data = json.loads(text or "")
    except json.JSONDecodeError:
        m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", text or "")
        return m.group(1) if m else ""
    items = data if isinstance(data, list) else [data]
    for item in items:
        if isinstance(item, dict):
            for key in ("ipAddress", "ip_address", "ip", "IPAddress"):
                if item.get(key):
                    return str(item[key])
    return ""


# ---- checks -----------------------------------------------------------------------------------

@dataclass
class CheckResult:
    passed: bool
    detail: str


SshFn = Callable[[str], subprocess.CompletedProcess]


def _osascript(script: str) -> str:
    return "osascript " + " ".join(f"-e {shlex.quote(line)}" for line in script.splitlines()
                                   if line.strip())


def _path(p: str) -> str:
    """Quote a path for the remote shell, keeping ~ expandable."""
    p = str(p)
    if p.startswith("~/"):
        return '"$HOME"/' + shlex.quote(p[2:])
    return shlex.quote(p)


def evaluate(check: dict[str, Any], ssh: SshFn, answer: str = "") -> CheckResult:
    """Score one check against the VM's real state (and the agent's answer)."""
    kind = next((k for k in check if k in CHECK_TYPES), "")
    expect = str(check.get("expect", ""))
    if kind == "all":
        results = [evaluate(c, ssh, answer) for c in check["all"]]
        failed = [r.detail for r in results if not r.passed]
        return CheckResult(not failed, "; ".join(failed) or "all checks passed")
    if kind == "answer_contains":
        want = str(check["answer_contains"])
        ok = want.casefold() in (answer or "").casefold()
        return CheckResult(ok, f"answer {'contains' if ok else 'lacks'} {want!r}")
    if kind == "answer_regex":
        ok = re.search(str(check["answer_regex"]), answer or "", re.I) is not None
        return CheckResult(ok, f"answer {'matches' if ok else 'does not match'} "
                               f"{check['answer_regex']!r}")
    if kind == "applescript":
        cmd = _osascript(str(check["applescript"]))
    elif kind == "shell":
        cmd = str(check["shell"])
    elif kind == "frontmost":
        cmd = _osascript('tell application "System Events" to get name of first process '
                         "whose frontmost is true")
        expect = str(check["frontmost"])
    elif kind == "file_exists":
        cmd = f"test -e {_path(check['file_exists'])} && echo present"
        expect = "present"
    elif kind == "file_missing":
        cmd = f"test -e {_path(check['file_missing'])} || echo absent"
        expect = "absent"
    elif kind == "file_contains":
        spec = check["file_contains"]
        cmd = f"cat {_path(spec['path'])}"
        expect = str(spec["text"])
    else:
        return CheckResult(False, f"unknown check {sorted(check)}")
    res = ssh(cmd)
    out = (res.stdout or "").strip()
    ok = res.returncode == 0 and expect.casefold() in out.casefold()
    shown = out.splitlines()[0][:80] if out else (res.stderr or "").strip()[:80]
    return CheckResult(ok, f"{kind}: got {shown!r}, want {expect!r}")


# ---- one task --------------------------------------------------------------------------------

@dataclass
class LiveResult:
    id: str
    passed: bool
    reason: str
    seconds: float = 0.0
    agent_status: str = ""
    answer: str = ""
    tools: list[str] = field(default_factory=list)


def run_goal(ssh: Callable[..., subprocess.CompletedProcess], goal: str, config: dict[str, Any],
             timeout: float = 900.0) -> dict[str, Any]:
    """POST /run to the sidecar inside the VM (through SSH) and return its JSON."""
    side = config.get("sidecar") or {}
    body = json.dumps({"goal": goal, "stream": False, "local_only": False})
    token = side.get("token", "")
    auth = f"-H {shlex.quote('Authorization: Bearer ' + token)} " if token else ""
    cmd = (f"curl -s -m {int(timeout)} -X POST {shlex.quote(side.get('url', 'http://127.0.0.1:8765'))}"
           f"/run -H 'Content-Type: application/json' {auth}-d {shlex.quote(body)}")
    res = ssh(cmd, timeout=timeout + 30)
    try:
        return json.loads(res.stdout or "{}")
    except json.JSONDecodeError:
        return {"status": "error", "error": (res.stdout or res.stderr or "no response")[:300]}


def run_task(task: dict[str, Any], vm: LumeVM, config: dict[str, Any]) -> LiveResult:
    started = time.monotonic()
    for cmd in task.get("setup") or []:
        res = vm.ssh(str(cmd))
        if res.returncode != 0:
            return LiveResult(task["id"], False, f"setup failed: {cmd!r}: "
                                                 f"{(res.stderr or '').strip()[:160]}")
    body = run_goal(vm.ssh, task["goal"], config, float(task.get("timeout", 900)))
    answer = str(body.get("result") or "")
    check = evaluate(task["check"], lambda c: vm.ssh(c, timeout=60), answer)
    for cmd in task.get("cleanup") or []:
        vm.ssh(str(cmd))
    status = str(body.get("status", ""))
    reason = check.detail if not body.get("error") else f"{check.detail} (agent error: " \
        f"{str(body['error'])[:120]})"
    return LiveResult(task["id"], check.passed, reason, round(time.monotonic() - started, 1),
                      status, answer[:300])


def select_tasks(tasks: list[dict[str, Any]], only: list[str] | None = None,
                 have: list[str] | None = None) -> tuple[list[dict], list[dict]]:
    """(tasks to run, tasks skipped for a missing requirement)."""
    chosen = [t for t in tasks if not only or t["id"] in only]
    have_set = set(have or [])
    run = [t for t in chosen if set(t.get("requires") or []) <= have_set]
    skipped = [t for t in chosen if t not in run]
    return run, skipped


def run_suite(tasks: list[dict[str, Any]], config: dict[str, Any], *, reuse_vm: bool = False,
              keep: bool = False, vm_factory: Callable[[str], LumeVM] | None = None,
              log: Callable[[str], None] = print) -> list[LiveResult]:
    """Run tasks, each in a fresh clone (or all in one clone with reuse_vm)."""
    make = vm_factory or (lambda name: LumeVM(config, name))
    results: list[LiveResult] = []
    shared: LumeVM | None = None
    for i, task in enumerate(tasks, 1):
        vm = shared
        try:
            if vm is None:
                vm = make(f"aether-bench-{int(time.time())}-{i}")
                vm.create()
                vm.start()
                vm.wait_ready(float(config.get("boot_timeout", 240)))
                if config.get("after_boot"):
                    vm.ssh(str(config["after_boot"]))
                    vm.sleep(float(config.get("after_boot_wait", 10)))
                if reuse_vm:
                    shared = vm
            log(f"[{i}/{len(tasks)}] {task['id']}: {task['goal']}")
            result = run_task(task, vm, config)
        except Exception as e:  # noqa: BLE001 — one broken VM must not end the suite
            result = LiveResult(task["id"], False, f"harness error: {e}")
        results.append(result)
        log(f"    {'PASS' if result.passed else 'FAIL'} ({result.seconds}s) {result.reason}")
        if vm is not None and not reuse_vm and not keep:
            vm.destroy()
    if shared is not None and not keep:
        shared.destroy()
    return results


def summarize_live(results: list[LiveResult], skipped: list[dict] | None = None) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    rate = round(100.0 * passed / total, 1) if total else 0.0
    return {
        "mode": "vm", "total": total, "passed": passed, "failed": total - passed,
        "pass_rate_pct": rate, "bar_pct": PASS_BAR_PCT, "meets_bar": rate >= PASS_BAR_PCT,
        "skipped": [t["id"] for t in skipped or []],
        "results": [r.__dict__ for r in results],
    }
