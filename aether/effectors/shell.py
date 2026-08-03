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

    def summary(self, limit: int = 1500) -> str:
        out = (self.stdout or "").strip()
        err = (self.stderr or "").strip()
        body = out if out else err
        if len(body) > limit:
            body = body[:limit] + "\n…(truncated)"
        return f"exit={self.returncode}\n{body}" if body else f"exit={self.returncode}"


def is_destructive(command: str) -> bool:
    return bool(_DESTRUCTIVE_RE.search(command or ""))


def run(command: str, timeout: int = 60, cwd: str | None = None) -> ShellResult:
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd,
        )
        return ShellResult(proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired:
        return ShellResult(124, "", f"Timed out after {timeout}s")
    except Exception as e:  # noqa: BLE001
        return ShellResult(1, "", str(e))


if __name__ == "__main__":
    r = run("echo hello from aether && uname -a")
    print(r.summary())
