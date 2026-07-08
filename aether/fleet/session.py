"""Agent session base — one live CLI agent subprocess with buffered output.

Thread-based (not asyncio): tool handlers are sync and run via
asyncio.to_thread, so sessions use reader threads like MetricsCollector.
"""
from __future__ import annotations

import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from ..core.metrics import MetricsCollector
from ..tools.delegation import build_subprocess_env

# States a session moves through
ACTIVE_STATES = ("starting", "running", "awaiting_input")
FINAL_STATES = ("done", "error", "stopped", "timeout")


@dataclass
class OutputEvent:
    seq: int
    ts: float
    kind: str  # text | tool | result | stderr | status
    content: str

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "ts": self.ts, "kind": self.kind, "content": self.content}


class AgentSession:
    """Base class for a supervised agent subprocess."""

    def __init__(
        self,
        *,
        session_id: str,
        label: str,
        agent_type: str,
        prompt: str,
        workspace: str,
        env_allowlist: list[str] | None = None,
        buffer_lines: int = 2000,
        timeout_sec: float = 1800.0,
        cost_cap_usd: float = 0.0,
    ) -> None:
        self.session_id = session_id
        self.label = label
        self.agent_type = agent_type
        self.prompt = prompt
        self.workspace = workspace
        self.env_allowlist = env_allowlist
        self.timeout_sec = timeout_sec
        self.cost_cap_usd = cost_cap_usd
        self.worktree_branch: str | None = None
        self.worktree_dir: str | None = None

        self.state = "starting"
        self.created_at = time.time()
        self.last_activity = time.time()
        self.cost_usd = 0.0
        self.exit_code: int | None = None
        self.result_summary = ""
        self.last_turn_error = False  # last completed turn reported is_error

        self._lock = threading.RLock()
        self._events: deque[OutputEvent] = deque(maxlen=max(100, buffer_lines))
        self._seq = 0
        self._proc: subprocess.Popen | None = None
        self._on_event: Callable[[AgentSession, OutputEvent], None] | None = None
        self._stopping = False
        self._respawning = False  # HeadlessSession follow-up in flight

    # ---- lifecycle (subclasses implement start; send optional) ----

    def start(self) -> None:
        raise NotImplementedError

    def send(self, text: str) -> bool:  # noqa: ARG002
        """Steer the agent with a follow-up. Default: unsupported."""
        return False

    def stop(self, grace_sec: float = 5.0, *, final_state: str = "stopped") -> None:
        """SIGINT → SIGTERM → SIGKILL escalation."""
        with self._lock:
            if self.state in FINAL_STATES or self._respawning:
                return
            self._stopping = True
            proc = self._proc
        if proc is not None and proc.poll() is None:
            for sig, wait in ((signal.SIGINT, grace_sec), (signal.SIGTERM, 2.0)):
                try:
                    proc.send_signal(sig)
                except (ProcessLookupError, OSError):
                    break
                try:
                    proc.wait(timeout=wait)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if proc.poll() is None:
                try:
                    proc.kill()
                except (ProcessLookupError, OSError):
                    pass
        self._set_state(final_state)

    # ---- output buffer ----

    def _emit(self, kind: str, content: str) -> None:
        with self._lock:
            self._seq += 1
            ev = OutputEvent(seq=self._seq, ts=time.time(), kind=kind, content=content)
            self._events.append(ev)
            self.last_activity = ev.ts
            sink = self._on_event
        if sink is not None:
            try:
                sink(self, ev)
            except Exception:  # noqa: BLE001 — sink must never kill the reader
                pass

    def _set_state(self, state: str) -> None:
        with self._lock:
            if self.state == state or self.state in FINAL_STATES:
                return
            self.state = state
        self._emit("status", state)

    def _add_cost(self, usd: float) -> None:
        if usd <= 0:
            return
        with self._lock:
            self.cost_usd += usd
        try:
            MetricsCollector.get().record_agent_cost(self.agent_type, usd)
        except Exception:  # noqa: BLE001
            pass

    def tail(self, n: int = 40) -> list[OutputEvent]:
        with self._lock:
            return list(self._events)[-n:]

    def events_since(self, seq: int) -> list[OutputEvent]:
        with self._lock:
            return [e for e in self._events if e.seq > seq]

    def is_active(self) -> bool:
        return self.state in ACTIVE_STATES

    def status_dict(self) -> dict[str, Any]:
        with self._lock:
            last = self._events[-1].content if self._events else ""
            return {
                "session_id": self.session_id,
                "label": self.label,
                "agent_type": self.agent_type,
                "state": self.state,
                "workspace": self.workspace,
                "worktree_branch": self.worktree_branch,
                "created_at": self.created_at,
                "elapsed_sec": round(time.time() - self.created_at, 1),
                "last_activity": self.last_activity,
                "cost_usd": round(self.cost_usd, 6),
                "exit_code": self.exit_code,
                "result_summary": self.result_summary[:500],
                "last_output": last[:200],
                "last_seq": self._seq,
            }


class SubprocessSession(AgentSession):
    """Shared pipe-based subprocess plumbing (claude / headless CLIs)."""

    def _spawn(self, cmd: list[str], *, stdin_pipe: bool = False) -> None:
        env = build_subprocess_env(self.env_allowlist)
        self._proc = subprocess.Popen(  # noqa: S603 — commands come from a fixed table
            cmd,
            cwd=self.workspace,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if stdin_pipe else subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._read_stdout, daemon=True,
                         name=f"fleet-out-{self.session_id}").start()
        threading.Thread(target=self._read_stderr, daemon=True,
                         name=f"fleet-err-{self.session_id}").start()
        self._set_state("running")

    def _handle_line(self, line: str) -> None:
        self._emit("text", line)

    def _read_stdout(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                if line:
                    self._handle_line(line)
        except (ValueError, OSError):
            pass  # pipe closed during stop
        code = proc.wait()
        self._on_exit(code)

    def _read_stderr(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        try:
            for line in proc.stderr:
                line = line.rstrip("\n")
                if line:
                    self._emit("stderr", line[:1000])
        except (ValueError, OSError):
            pass

    def _on_exit(self, code: int) -> None:
        with self._lock:
            self.exit_code = code
            stopping = self._stopping
        if stopping or self.state in FINAL_STATES:
            return
        self._set_state("done" if code == 0 else "error")
