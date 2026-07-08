"""Claude Code session via `claude -p` stream-json (NDJSON in/out)."""
from __future__ import annotations

import json

from .session import SubprocessSession


class ClaudeCodeSession(SubprocessSession):
    """Multi-turn Claude Code subprocess; steerable via stdin NDJSON."""

    def __init__(
        self,
        *,
        binary: str = "claude",
        permission_mode: str = "acceptEdits",
        allowed_tools: list[str] | None = None,
        allow_bypass_permissions: bool = False,
        mcp_config: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.binary = binary
        self.permission_mode = permission_mode
        self.allowed_tools = allowed_tools or []
        self.allow_bypass_permissions = allow_bypass_permissions
        self.mcp_config = mcp_config
        self.claude_session_id: str | None = None  # for --resume recovery

    def start(self) -> None:
        cmd = [
            self.binary, "-p",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
        ]
        # bypassPermissions only inside an isolated worktree — enforced by manager
        if self.permission_mode:
            cmd += ["--permission-mode", self.permission_mode]
        if self.allowed_tools:
            cmd += ["--allowedTools", ",".join(self.allowed_tools)]
        if self.mcp_config:
            cmd += ["--mcp-config", self.mcp_config]
        self._spawn(cmd, stdin_pipe=True)
        self._write_user_message(self.prompt)

    def send(self, text: str) -> bool:
        with self._lock:
            proc = self._proc
            ok = self.state in ("running", "awaiting_input") and proc is not None
        if not ok or proc.poll() is not None:
            return False
        self._write_user_message(text)
        self._set_state("running")
        return True

    def _write_user_message(self, text: str) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return
        msg = {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        }
        try:
            proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass  # process died; exit handler sets final state

    # Tolerant NDJSON parser — unknown/invalid lines become raw text events,
    # so stream-json format drift across claude CLI versions degrades gracefully.
    def _handle_line(self, line: str) -> None:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            self._emit("text", line)
            return
        if not isinstance(obj, dict):
            self._emit("text", line)
            return
        kind = obj.get("type")
        if kind == "system":
            sid = obj.get("session_id")
            if sid:
                self.claude_session_id = str(sid)
        elif kind == "assistant":
            for block in (obj.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text"):
                    self._emit("text", block["text"])
                elif block.get("type") == "tool_use":
                    args = json.dumps(block.get("input") or {}, ensure_ascii=False)
                    self._emit("tool", f"{block.get('name', '?')} {args[:300]}")
        elif kind == "result":
            cost = obj.get("total_cost_usd")
            if isinstance(cost, (int, float)):
                # total_cost_usd is cumulative across turns — record the delta
                self._add_cost(float(cost) - self.cost_usd)
            summary = str(obj.get("result") or obj.get("subtype") or "done")
            is_error = bool(obj.get("is_error")) or str(obj.get("subtype", "")).startswith("error")
            with self._lock:
                self.result_summary = summary
                self.last_turn_error = is_error  # graph nodes treat an errored turn as a failure
            self._emit("result", ("ERROR: " if is_error else "") + summary)
            # stream-json input keeps stdin open: session waits for next turn
            self._set_state("awaiting_input")
        else:
            self._emit("text", line[:1000])
