"""Run a self-written tool in its own Python process, inside a Seatbelt sandbox.

Each run gets a fresh scratch folder (its only writable place besides the
manifest's write_dirs) and a copy of the tool's code, so the tool cannot
modify itself or read Aether's data folder. The profile also denies network
unless the manifest allows it, other programs, Apple Events, LaunchServices
and signals, and keeps credential stores unreadable. A tool with internet
access can read file contents only under the system and Python folders and
the manifest's read_dirs and write_dirs.

Without the sandbox (not macOS, or ``sandbox.enabled: false``) tools do not
run at all, unless ``toolsmith.allow_unsandboxed`` is set for tests.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..effectors import sandbox
from .manifest import ToolManifest
from .settings import Settings

RUNNER = Path(__file__).with_name("runner.py")
TAIL_BYTES = 64 * 1024

# Readable by a tool with internet access (system libraries, Python, certificates).
SYSTEM_READ_ROOTS = (
    "/System", "/usr", "/bin", "/sbin", "/Library/Frameworks", "/Library/Preferences",
    "/Library/Developer/CommandLineTools", "/Applications/Xcode.app/Contents/Developer",
    "/opt/homebrew", "/usr/local", "/private/etc", "/private/var/db/dyld",
    "/private/var/db/timezone", "/dev",
)


@dataclass
class RunResult:
    ok: bool
    kind: str            # ok | error | denied | timeout | crashed | stopped | unavailable
    output: str          # the tool's result when ok, else what went wrong
    error_type: str = ""
    traceback: str = ""
    log: str = ""
    truncated: bool = False
    duration_ms: int = 0
    sandboxed: bool = False

    def text(self, name: str) -> str:
        """What the model sees."""
        if self.ok:
            body = self.output if self.output.strip() else "(the tool returned nothing)"
            return body + ("\n[output truncated]" if self.truncated else "")
        return f"ERROR: {name} failed ({self.kind}): {self.output}"


def can_run(settings: Settings) -> tuple[bool, str]:
    if sandbox.enabled("shell", {**settings.sandbox, "shell": True}):
        return True, "sandboxed"
    if settings.allow_unsandboxed:
        return True, "unsandboxed (toolsmith.allow_unsandboxed)"
    return False, ("self-written tools run only inside the macOS sandbox, which is off or "
                   "not available here")


def _both(paths: set[str]) -> list[str]:
    out = set()
    for r in paths:
        if r:
            out.add(sandbox.canon(r))
            out.add(os.path.abspath(os.path.expanduser(r)))
    return sorted(out)


def python_exec_roots() -> list[str]:
    """Folders the interpreter may be started from (framework builds re-exec
    Python.app inside the base prefix)."""
    return _both({sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix,
                  os.path.dirname(sys.executable)})


def python_read_roots() -> list[str]:
    """Where this interpreter and its standard library live."""
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix,
             os.path.dirname(sys.executable), os.path.dirname(os.__file__)}
    try:
        paths = ssl.get_default_verify_paths()
        for p in (paths.cafile, paths.capath, paths.openssl_cafile, paths.openssl_capath):
            if p:
                roots.add(os.path.dirname(p) if os.path.isfile(p) else p)
    except (AttributeError, ValueError):
        pass
    return _both(roots)


def tool_profile(manifest: ToolManifest, scratch: str, code_dir: str,
                 settings: Settings, *, home: str | None = None) -> sandbox.Profile:
    home = home or str(Path.home())
    s = {**settings.sandbox, "extra_deny_read": list(settings.sandbox.get("extra_deny_read") or [])}
    pw, pr = sandbox._protected(s, home)  # noqa: SLF001 — same protected set as shell
    from ..core.paths import data_dir

    pr = tuple(dict.fromkeys((*pr, sandbox.canon(data_dir()))))
    writes = [sandbox.canon(scratch)] + [sandbox.canon(d) for d in manifest.write_dirs]
    network = bool(manifest.network and settings.network_cap)
    read_roots = None
    if manifest.network and settings.restrict_reads_with_network:
        reads = [*SYSTEM_READ_ROOTS, *python_read_roots(), sandbox.canon(code_dir), *writes,
                 *(sandbox.canon(d) for d in manifest.read_dirs),
                 *(sandbox.canon(d) for d in settings.extra_read_roots)]
        read_roots = tuple(dict.fromkeys(reads))
    return sandbox.Profile(f"tool {manifest.name}", tuple(dict.fromkeys(writes)), pw, pr,
                           network, read_roots=read_roots, no_fork=True,
                           exec_roots=tuple(python_exec_roots()))


def _tail(path: Path) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - TAIL_BYTES))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _payload(stdout: str, nonce: str) -> dict | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith(nonce):
            try:
                data = json.loads(line[len(nonce):])
            except ValueError:
                return None
            return data if isinstance(data, dict) else None
    return None


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


_DENIED_MARKERS = ("operation not permitted", "errno 1]", "sandbox", "deny(")


def run_tool(manifest: ToolManifest, code: str, args: dict, settings: Settings, *,
             should_stop: Callable[[], bool] | None = None) -> RunResult:
    ok, why = can_run(settings)
    if not ok:
        return RunResult(False, "unavailable", why)
    if not settings.shell_cap:
        return RunResult(False, "unavailable", "the shell capability is off in config.yaml")
    sandboxed = why == "sandboxed"
    timeout = settings.timeout_s
    work = Path(tempfile.mkdtemp(prefix="aether-tool-"))
    try:
        scratch, code_dir = work / "scratch", work / "code"
        scratch.mkdir()
        code_dir.mkdir()
        shutil.copy2(RUNNER, code_dir / "runner.py")
        (code_dir / "tool.py").write_text(code, encoding="utf-8")
        argv = [sys.executable, "-I", "-S", "-B", str(code_dir / "runner.py"),
                str(code_dir / "tool.py"), str(timeout)]
        if sandboxed:
            argv = tool_profile(manifest, str(scratch), str(code_dir), settings).wrap(argv)
        env = sandbox.child_env()
        env["TMPDIR"] = str(scratch) + "/"
        nonce = secrets.token_hex(16)
        request = json.dumps({"nonce": nonce, "args": args}).encode("utf-8")
        started = time.monotonic()
        kind = ""
        with open(work / "stdout", "wb") as out, open(work / "stderr", "wb") as err:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                    cwd=scratch, env=env, start_new_session=True)
            try:
                assert proc.stdin is not None
                proc.stdin.write(request)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            deadline = started + timeout
            while proc.poll() is None:
                if should_stop is not None and should_stop():
                    kind = "stopped"
                elif time.monotonic() > deadline:
                    kind = "timeout"
                if kind:
                    _kill(proc)
                    break
                time.sleep(0.05)
        duration = int((time.monotonic() - started) * 1000)
        if kind == "stopped":
            return RunResult(False, "stopped", "stopped by the user", duration_ms=duration,
                             sandboxed=sandboxed)
        if kind == "timeout":
            return RunResult(False, "timeout", f"it ran longer than {timeout} s and was stopped",
                             duration_ms=duration, sandboxed=sandboxed)
        data = _payload(_tail(work / "stdout"), nonce)
        if data is None:
            stderr = _tail(work / "stderr").strip()
            detail = stderr[-1500:] or f"the process exited with code {proc.returncode}"
            low = detail.lower()
            crashed_kind = ("denied" if sandboxed and any(m in low for m in _DENIED_MARKERS)
                            else "crashed")
            return RunResult(False, crashed_kind, detail, duration_ms=duration,
                             sandboxed=sandboxed)
        if data.get("ok"):
            return RunResult(True, "ok", str(data.get("result", "")),
                             truncated=bool(data.get("truncated")), log=str(data.get("log", "")),
                             duration_ms=duration, sandboxed=sandboxed)
        error = str(data.get("error", "unknown error"))
        error_type = str(data.get("error_type", ""))
        denied = sandboxed and (error_type == "PermissionError"
                                or any(m in error.lower() for m in _DENIED_MARKERS[:2]))
        return RunResult(False, "denied" if denied else "error", error, error_type=error_type,
                         traceback=str(data.get("traceback", "")), log=str(data.get("log", "")),
                         duration_ms=duration, sandboxed=sandboxed)
    finally:
        shutil.rmtree(work, ignore_errors=True)
