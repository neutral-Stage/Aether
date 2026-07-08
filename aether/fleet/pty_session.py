"""PTY-backed session for interactive TUIs and persistent terminals.

Best-effort transport: constantly-redrawing TUIs produce noisy buffers.
Output is ANSI-stripped and coalesced; awaiting_input is a quiescence
heuristic (no output for idle_detect_sec).
"""
from __future__ import annotations

import os
import pty
import re
import subprocess
import threading
import time

from .session import AgentSession
from ..tools.delegation import build_subprocess_env

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[=>]|\r")

PTY_COMMANDS: dict[str, list[str]] = {
    "kilo": ["kilo"],
}


class PTYSession(AgentSession):
    """Generic PTY session; agent_type 'terminal' spawns the user's shell."""

    def __init__(self, *, idle_detect_sec: float = 3.0, **kwargs) -> None:
        super().__init__(**kwargs)
        self.idle_detect_sec = idle_detect_sec
        self._master_fd: int | None = None

    def start(self) -> None:
        if self.agent_type == "terminal":
            cmd = [os.environ.get("SHELL", "/bin/zsh"), "-i"]
        else:
            cmd = PTY_COMMANDS.get(self.agent_type)
        if cmd is None:
            self._emit("stderr", f"unknown PTY agent: {self.agent_type}")
            self._set_state("error")
            return
        master, slave = pty.openpty()
        self._master_fd = master
        env = build_subprocess_env(self.env_allowlist)
        env["TERM"] = "dumb"  # discourage TUI redraw noise
        try:
            self._proc = subprocess.Popen(  # noqa: S603
                cmd,
                cwd=self.workspace,
                env=env,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
            )
        except BaseException:
            os.close(master)  # don't leak the master fd if spawn fails
            self._master_fd = None
            raise
        finally:
            os.close(slave)
        threading.Thread(target=self._read_master, daemon=True,
                         name=f"fleet-pty-{self.session_id}").start()
        threading.Thread(target=self._idle_watch, daemon=True,
                         name=f"fleet-idle-{self.session_id}").start()
        self._set_state("running")
        if self.prompt:
            # initial command/prompt goes down the wire once the process is up
            time.sleep(0.2)
            self.send(self.prompt)

    def send(self, text: str) -> bool:
        fd = self._master_fd
        proc = self._proc
        if fd is None or proc is None or proc.poll() is not None:
            return False
        try:
            os.write(fd, (text.rstrip("\n") + "\n").encode())
        except OSError:
            return False
        self._set_state("running")
        return True

    def _read_master(self) -> None:
        fd = self._master_fd
        assert fd is not None and self._proc is not None
        buf = b""
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                text = _ANSI_RE.sub("", raw.decode("utf-8", errors="replace")).rstrip()
                if text:
                    self._emit("text", text[:1000])
        if buf:
            text = _ANSI_RE.sub("", buf.decode("utf-8", errors="replace")).rstrip()
            if text:
                self._emit("text", text[:1000])
        code = self._proc.wait()
        with self._lock:
            self.exit_code = code
            stopping = self._stopping
        try:
            os.close(fd)
        except OSError:
            pass
        if not stopping:
            self._set_state("done" if code == 0 else "error")

    def _idle_watch(self) -> None:
        while True:
            time.sleep(1.0)
            with self._lock:
                state = self.state
                idle = time.time() - self.last_activity
            if state in ("done", "error", "stopped", "timeout"):
                return
            if state == "running" and idle >= self.idle_detect_sec:
                self._set_state("awaiting_input")
