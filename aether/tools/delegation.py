"""Tier-0 delegation to coding CLIs (§6.10, Phase 11 FR-19)."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Known agent CLIs — checked in order when agent=auto
AGENT_COMMANDS: dict[str, list[str]] = {
    "claude": ["claude", "-p"],
    "codex": ["codex", "exec"],
    "opencode": ["opencode", "run"],
    "cursor": ["cursor", "agent"],
}

# Safe defaults: strip sensitive env vars from subprocess
_DEFAULT_ENV_ALLOWLIST = frozenset({
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "NO_COLOR",
    "TERM",
    "PWD",
})


@dataclass
class DelegationResult:
    stdout: str
    stderr: str
    exit_code: int
    summary: str
    cwd: str
    agent: str
    timed_out: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def format_for_model(self) -> str:
        """Human-readable block for the LLM."""
        if self.error and self.exit_code != 0:
            return f"ERROR: {self.error}"
        parts = [
            f"exit_code={self.exit_code}",
            f"cwd={self.cwd}",
            f"agent={self.agent}",
            f"summary={self.summary}",
        ]
        if self.stdout:
            parts.append("--- stdout ---\n" + self.stdout.strip()[:8000])
        if self.stderr:
            parts.append("--- stderr ---\n" + self.stderr.strip()[:4000])
        return "\n".join(parts)

    def format_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def delegate_to_coder(
    prompt: str,
    *,
    agent: str = "auto",
    workspace: str | None = None,
    timeout_sec: int = 300,
    env_allowlist: list[str] | None = None,
    structured: bool = True,
    approved_roots: list[str] | None = None,
) -> str:
    """Run a supervised coding-agent CLI subprocess and return output."""
    result = delegate_to_coder_structured(
        prompt,
        agent=agent,
        workspace=workspace,
        timeout_sec=timeout_sec,
        env_allowlist=env_allowlist,
        approved_roots=approved_roots,
    )
    if structured:
        return result.format_json()
    return result.format_for_model()


def delegate_to_coder_structured(
    prompt: str,
    *,
    agent: str = "auto",
    workspace: str | None = None,
    timeout_sec: int = 300,
    env_allowlist: list[str] | None = None,
    approved_roots: list[str] | None = None,
) -> DelegationResult:
    """Run delegation and return structured result."""
    prompt = sanitize_prompt(prompt)
    if not prompt:
        return DelegationResult(
            stdout="",
            stderr="",
            exit_code=1,
            summary="prompt is required",
            cwd="",
            agent=agent,
            error="prompt is required",
        )

    cmd = _resolve_agent_command(agent)
    resolved_agent = agent
    if cmd is None:
        if agent == "auto":
            resolved_agent = "none"
        return DelegationResult(
            stdout="",
            stderr="",
            exit_code=127,
            summary=f"no coding CLI found for agent={agent!r}",
            cwd="",
            agent=resolved_agent,
            error=(
                f"no coding CLI found for agent={agent!r}. "
                f"Install one of: {', '.join(AGENT_COMMANDS)}"
            ),
        )

    cwd_path, cwd_err = _resolve_workspace(workspace, approved_roots)
    if cwd_err:
        return DelegationResult(
            stdout="",
            stderr="",
            exit_code=1,
            summary=cwd_err,
            cwd=str(cwd_path) if cwd_path else "",
            agent=resolved_agent,
            error=cwd_err,
        )

    full_cmd = cmd + [prompt]
    env = _build_sandbox_env(env_allowlist)
    cwd = str(cwd_path)

    # Popen (not subprocess.run) so the global STOP can kill it in-flight: we
    # register a closer that terminates the process, and also poll stop_ctl in
    # the wait loop (belt-and-suspenders for the register-after-STOP race).
    from ..core import stop as stop_ctl
    try:
        proc = subprocess.Popen(  # noqa: S603
            full_cmd, cwd=cwd, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        return DelegationResult(
            stdout="", stderr="", exit_code=127,
            summary=f"command not found: {full_cmd[0]}",
            cwd=cwd, agent=full_cmd[0], error=f"command not found: {full_cmd[0]}",
        )

    def _killpg(sig: int) -> None:
        # start_new_session=True gave the child its own process group; signal the
        # WHOLE group so a coder's git/npm/test grandchildren die too, not just
        # the CLI leader (else they orphan and keep mutating the workspace).
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, OSError):
            try:
                proc.terminate() if sig != signal.SIGKILL else proc.kill()
            except (ProcessLookupError, OSError):
                pass

    def _kill() -> None:
        _killpg(signal.SIGTERM)

    stop_ctl.register_closer(_kill)
    try:
        if stop_ctl.is_set():
            _kill()
        deadline = time.monotonic() + timeout_sec
        raw_out = raw_err = ""
        while True:
            remaining = deadline - time.monotonic()
            try:
                raw_out, raw_err = proc.communicate(timeout=min(0.2, max(0.0, remaining)))
                break
            except subprocess.TimeoutExpired:
                if stop_ctl.is_set():
                    _kill()
                    try:
                        proc.communicate(timeout=2)
                    except subprocess.TimeoutExpired:
                        _killpg(signal.SIGKILL)
                    return DelegationResult(
                        stdout="", stderr="", exit_code=130,
                        summary="stopped by user", cwd=cwd, agent=full_cmd[0],
                        error="STOP — delegation cancelled",
                    )
                if remaining <= 0:
                    _kill()
                    try:
                        proc.communicate(timeout=2)
                    except subprocess.TimeoutExpired:
                        _killpg(signal.SIGKILL)
                    return DelegationResult(
                        stdout="", stderr="", exit_code=124,
                        summary=f"timed out after {timeout_sec}s",
                        cwd=cwd, agent=full_cmd[0], timed_out=True,
                        error=f"{full_cmd[0]} timed out after {timeout_sec}s",
                    )
        stdout = (raw_out or "")[:8000]
        stderr = (raw_err or "")[:4000]
        summary = _summarize_output(stdout, stderr, proc.returncode)
        return DelegationResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
            summary=summary,
            cwd=cwd,
            agent=resolved_agent if agent != "auto" else full_cmd[0],
        )
    except Exception as exc:  # noqa: BLE001
        return DelegationResult(
            stdout="", stderr="", exit_code=1, summary=str(exc),
            cwd=cwd, agent=full_cmd[0], error=f"running {full_cmd[0]}: {exc}",
        )
    finally:
        stop_ctl.unregister_closer(_kill)


def _resolve_workspace(
    workspace: str | None,
    approved_roots: list[str] | None,
) -> tuple[Path | None, str | None]:
    cwd = Path(workspace).expanduser().resolve() if workspace else Path.cwd().resolve()
    if not cwd.is_dir():
        return cwd, f"workspace not found: {cwd}"

    roots = [Path(r).expanduser().resolve() for r in (approved_roots or [])]
    if roots:
        allowed = any(
            cwd == root or root in cwd.parents for root in roots
        )
        if not allowed:
            return cwd, f"workspace outside approved roots: {cwd}"
    return cwd, None


def sanitize_prompt(prompt: str) -> str:
    """Strip null bytes and normalize delegation prompts."""
    return (prompt or "").replace("\x00", "").strip()


def build_subprocess_env(allowlist: list[str] | None = None) -> dict[str, str]:
    """Public wrapper for sandboxed subprocess environment."""
    return _build_sandbox_env(allowlist)


def _build_sandbox_env(allowlist: list[str] | None) -> dict[str, str]:
    allowed = _DEFAULT_ENV_ALLOWLIST | frozenset(allowlist or [])
    env: dict[str, str] = {}
    for key in allowed:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    env.setdefault("NO_COLOR", "1")
    # Propagate + increment spawn depth so a child that itself runs Aether is
    # caught by the fork-bomb guard in SessionManager.spawn (Phase 9).
    try:
        env["AETHER_SPAWN_DEPTH"] = str(int(os.environ.get("AETHER_SPAWN_DEPTH", "0")) + 1)
    except ValueError:
        env["AETHER_SPAWN_DEPTH"] = "1"
    return env


def _summarize_output(stdout: str, stderr: str, exit_code: int) -> str:
    if exit_code == 0:
        line = (stdout or stderr).strip().splitlines()
        if line:
            return line[-1][:200]
        return "completed successfully"
    err_line = (stderr or stdout).strip().splitlines()
    if err_line:
        return err_line[-1][:200]
    return f"failed with exit code {exit_code}"


def _resolve_agent_command(agent: str) -> list[str] | None:
    if agent != "auto" and agent in AGENT_COMMANDS:
        base = AGENT_COMMANDS[agent]
        return base if _which(base[0]) else None
    for _name, base in AGENT_COMMANDS.items():
        if _which(base[0]):
            return base
    return None


def _which(name: str) -> bool:
    from shutil import which
    return which(name) is not None


def write_spec_file(prompt: str, workspace: str | None = None) -> str:
    """Write a delegation spec to a temp file (for agents that prefer file input)."""
    path = Path(tempfile.gettempdir()) / f"aether-delegate-{os.getpid()}.md"
    path.write_text(prompt, encoding="utf-8")
    return str(path)
