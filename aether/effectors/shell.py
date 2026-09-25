"""Run shell commands, with a coarse destructive-command heuristic."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

# Patterns that should require explicit user confirmation in Phase 0.
_DESTRUCTIVE = [
    r"\brm\s+-rf?\b", r"\bsudo\b", r"\bmkfs\b", r"\bdd\s+if=",
    r"\bshutdown\b", r"\breboot\b", r"\bkillall\b", r">\s*/dev/sd",
    r"\bgit\s+push\b", r"\bgit\s+reset\s+--hard\b", r"\bbrew\s+uninstall\b",
    r"\bdiskutil\b", r"\bchmod\s+-R\b", r"\bchown\s+-R\b", r"\bcurl\b.*\|\s*(sh|bash)",
    # fork bomb: a function body with a backgrounded pipe — `:(){ :|:& };:`,
    # `bomb(){ bomb|bomb& }` (the recursion token may be ':' or a name)
    r"\(\s*\)\s*\{[^}]*\|[^}]*&",
]
_DESTRUCTIVE_RE = re.compile("|".join(_DESTRUCTIVE), re.IGNORECASE)


@dataclass
class ShellResult:
    returncode: int
    stdout: str
    stderr: str
    note: str = ""          # e.g. why the sandbox blocked the command

    def summary(self, limit: int = 1500) -> str:
        out = (self.stdout or "").strip()
        err = (self.stderr or "").strip()
        body = out if out else err
        if len(body) > limit:
            body = body[:limit] + "\n…(truncated)"
        text = f"exit={self.returncode}\n{body}" if body else f"exit={self.returncode}"
        return f"{text}\n[{self.note}]" if self.note else text


def is_destructive(command: str) -> bool:
    return bool(_DESTRUCTIVE_RE.search(command or ""))


def run(command: str, timeout: int = 60, cwd: str | None = None, *,
        sandboxed: bool | None = None) -> ShellResult:
    """Run ``command`` with /bin/sh. On macOS it runs under Aether's Seatbelt
    profile (see sandbox.py) unless sandbox.shell is off or sandboxed=False."""
    from . import sandbox

    argv, profile = (["/bin/sh", "-c", command], None)
    if sandboxed is not False:
        argv, profile = sandbox.wrap_shell(command)
    try:
        proc = subprocess.run(  # noqa: S603 — argv is /bin/sh -c, optionally under sandbox-exec
            argv, capture_output=True, text=True, timeout=timeout, cwd=cwd,
            env=sandbox.child_env(),
        )
        note = ""
        if proc.returncode != 0:
            note = sandbox.explain_denial(proc.stderr, profile) or ""
        return ShellResult(proc.returncode, proc.stdout, proc.stderr, note)
    except subprocess.TimeoutExpired:
        return ShellResult(124, "", f"Timed out after {timeout}s")
    except Exception as e:  # noqa: BLE001
        return ShellResult(1, "", str(e))


if __name__ == "__main__":
    r = run("echo hello from aether && uname -a")
    print(r.summary())
