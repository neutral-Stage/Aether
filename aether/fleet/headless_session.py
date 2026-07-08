"""One-shot streamed CLI sessions: codex exec, opencode run, cursor agent."""
from __future__ import annotations

import json
import threading

from .session import SubprocessSession

# Commands take the prompt as the final argument
HEADLESS_COMMANDS: dict[str, list[str]] = {
    "codex": ["codex", "exec", "--json"],
    "opencode": ["opencode", "run"],
    "cursor": ["cursor", "agent"],
}


class HeadlessSession(SubprocessSession):
    """Streams a one-shot CLI. send() respawns with a follow-up (documented limitation)."""

    def start(self) -> None:
        cmd = HEADLESS_COMMANDS.get(self.agent_type)
        if cmd is None:
            self._emit("stderr", f"unknown headless agent: {self.agent_type}")
            self._set_state("error")
            return
        self._spawn(cmd + [self.prompt])

    def _handle_line(self, line: str) -> None:
        # codex --json emits NDJSON; surface message-ish fields, else raw
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            self._emit("text", line)
            return
        if isinstance(obj, dict):
            text = obj.get("text") or obj.get("message") or obj.get("content")
            if isinstance(text, str) and text:
                self._emit("text", text)
                return
        self._emit("text", line[:1000])

    def _on_exit(self, code: int) -> None:
        with self._lock:
            if not self.result_summary:
                lines = [e.content for e in self._events if e.kind == "text"]
                self.result_summary = lines[-1][:500] if lines else f"exit {code}"
        super()._on_exit(code)

    def send(self, text: str) -> bool:
        """Follow-up = respawn with previous output digest + new instruction."""
        with self._lock:
            if self.state not in ("done", "error"):
                return False
            digest = "\n".join(e.content for e in self.tail(20) if e.kind == "text")[-2000:]
            # _respawning parks the watchdog so it can't finalize state mid-respawn
            self._respawning = True
            self.state = "starting"
            self._stopping = False
        self.prompt = (
            f"Previous session output (digest):\n{digest}\n\nFollow-up instruction: {text}"
        )
        threading.Thread(target=self._respawn, daemon=True).start()
        return True

    def _respawn(self) -> None:
        try:
            self.start()
        except Exception as e:  # noqa: BLE001
            self._emit("stderr", f"respawn failed: {e}")
            self._set_state("error")
        finally:
            with self._lock:
                self._respawning = False
