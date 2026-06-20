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


def finder_go_to(path: str) -> str:
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
tell application "Finder"
    activate
    set target of front window to POSIX file "{escaped}"
end tell
'''
    return run_applescript(script).summary()


def safari_open_url(url: str) -> str:
    escaped = url.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
tell application "Safari"
    activate
    if (count of windows) = 0 then
        make new document
    end if
    set URL of front document to "{escaped}"
end tell
'''
    return run_applescript(script).summary()


def mail_compose(to: str = "", subject: str = "", body: str = "") -> str:
  # AppleScript strings need escaping
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    to_part = f'make new to recipient with properties {{address:"{esc(to)}"}}' if to else ""
    script = f'''
tell application "Mail"
    activate
    set newMessage to make new outgoing message with properties {{subject:"{esc(subject)}", content:"{esc(body)}", visible:true}}
    tell newMessage
        {to_part}
    end tell
end tell
'''
    return run_applescript(script).summary()


def mail_activate() -> str:
    return run_applescript('tell application "Mail" to activate').summary()
