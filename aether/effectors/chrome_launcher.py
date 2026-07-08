"""Relaunch the user's Chrome with remote debugging so Aether can attach (Phase 5).

Registered as a destructive-impact tool: it quits and restarts the user's
browser (tabs restore via Chrome's session restore).
"""
from __future__ import annotations

import subprocess
import time
import urllib.request

DEFAULT_PORT = 9222


def cdp_ready(port: int = DEFAULT_PORT, timeout_sec: float = 0.8) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=timeout_sec
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def launch_with_debug_port(port: int = DEFAULT_PORT, wait_sec: float = 8.0) -> str:
    """Quit Chrome if running, relaunch with --remote-debugging-port."""
    if cdp_ready(port):
        return f"Chrome already listening on CDP port {port}."
    # Graceful quit (AppleScript preserves session restore), ignore if not running.
    subprocess.run(  # noqa: S603
        ["osascript", "-e", 'tell application "Google Chrome" to quit'],
        capture_output=True, timeout=15, check=False,
    )
    time.sleep(1.5)
    subprocess.run(  # noqa: S603
        ["open", "-a", "Google Chrome", "--args",
         f"--remote-debugging-port={port}", "--restore-last-session"],
        capture_output=True, timeout=10, check=True,
    )
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        if cdp_ready(port):
            return (f"Chrome relaunched with CDP on port {port}. "
                    "browser_* tools now attach to the user's real tabs.")
        time.sleep(0.5)
    return (f"Chrome relaunched but CDP port {port} not answering yet — "
            "retry browser tools in a few seconds.")
