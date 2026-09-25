"""AppleScript / Apple Events effector (Tier 1 for Mail, Safari, Finder)."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class AppleScriptResult:
    returncode: int
    stdout: str
    stderr: str

    def summary(self, limit: int = 1500) -> str:
        out = (self.stdout or "").strip()
        err = (self.stderr or "").strip()
        body = out if out else err
        if len(body) > limit:
            body = body[:limit] + "\n…(truncated)"
        if self.returncode != 0:
            return f"AppleScript error (exit={self.returncode})\n{body}"
        return body or "OK"


def run_applescript(source: str, timeout: int = 30) -> AppleScriptResult:
    try:
        proc = subprocess.run(
            ["osascript", "-e", source],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return AppleScriptResult(proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired:
        return AppleScriptResult(124, "", f"Timed out after {timeout}s")
    except Exception as e:  # noqa: BLE001
        return AppleScriptResult(1, "", str(e))


def run_applescript_args(source: str, args: list[str], timeout: int = 10) -> AppleScriptResult:
    """Run ``source`` with ``args`` as its argv, never spliced into the script text.

    ``source`` reads them with ``on run argv`` / ``item N of argv``, so a value
    that happens to contain quotes or AppleScript syntax can't escape into the
    script. Same never-raise behaviour as :func:`run_applescript`.
    """
    try:
        proc = subprocess.run(
            ["osascript", "-e", source, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return AppleScriptResult(proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired:
        return AppleScriptResult(124, "", f"Timed out after {timeout}s")
    except Exception as e:  # noqa: BLE001
        return AppleScriptResult(1, "", str(e))


def as_applescript_string(s: str) -> str:
    """A quoted, escaped AppleScript string literal for ``s`` (e.g. ``"it\\"s"``)."""
    escaped = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def finder_go_to(path: str) -> str:
    script = f'''
tell application "Finder"
    activate
    set target of front window to POSIX file {as_applescript_string(path)}
end tell
'''
    return run_applescript(script).summary()


def safari_open_url(url: str) -> str:
    script = f'''
tell application "Safari"
    activate
    if (count of windows) = 0 then
        make new document
    end if
    set URL of front document to {as_applescript_string(url)}
end tell
'''
    return run_applescript(script).summary()


def mail_compose(to: str = "", subject: str = "", body: str = "") -> str:
    to_part = (f'make new to recipient with properties {{address:{as_applescript_string(to)}}}'
              if to else "")
    subject_lit = as_applescript_string(subject)
    body_lit = as_applescript_string(body)
    script = f'''
tell application "Mail"
    activate
    set newMessage to make new outgoing message with properties {{subject:{subject_lit}, content:{body_lit}, visible:true}}
    tell newMessage
        {to_part}
    end tell
end tell
'''
    return run_applescript(script).summary()


def mail_activate() -> str:
    return run_applescript('tell application "Mail" to activate').summary()
