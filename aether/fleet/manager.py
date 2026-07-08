"""SessionManager — singleton owning all fleet agent sessions."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from ..core.audit_log import AuditLog
from ..tools.delegation import _resolve_workspace
from . import worktree as wt
from .claude_session import ClaudeCodeSession
from .headless_session import HeadlessSession
from .pty_session import PTYSession
from .session import AgentSession, OutputEvent

AGENT_TYPES = ("claude", "codex", "opencode", "cursor", "kilo", "terminal")

_stop_bridge_registered = False


def register_stop_bridge() -> None:
    """Wire global STOP → fast-kill fleet subprocesses + cancel graphs. Idempotent;
    the listener resolves the current singletons each time so it survives reset()."""
    global _stop_bridge_registered
    if _stop_bridge_registered:
        return
    _stop_bridge_registered = True
    from ..core import stop as stop_ctl

    def _bridge() -> None:
        try:
            SessionManager.get().stop_all(grace_sec=0.5)
        except Exception:  # noqa: BLE001
            pass
        try:
            from .graph_runner import GraphManager
            GraphManager.get().cancel_all()
        except Exception:  # noqa: BLE001
            pass

    stop_ctl.on_stop(_bridge)

_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "max_sessions": 4,
    "output_buffer_lines": 2000,
    "session_timeout_sec": 1800,
    "cost_cap_usd": 5.0,
    "claude_permission_mode": "acceptEdits",
    "claude_allowed_tools": [],
    "allow_bypass_permissions": False,
    "env_allowlist": [],
    "idle_detect_sec": 3.0,
    "worktrees": {"enabled": True, "keep": True},
    "require_isolation": False,
    "max_spawn_depth": 2,
}


class SessionManager:
    """Thread-safe fleet registry with timeout/cost watchdog."""

    _instance: SessionManager | None = None
    _class_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, AgentSession] = {}
        self._cfg: dict[str, Any] = dict(_DEFAULTS)
        self._mcp_cfg: dict[str, Any] = {}
        self._approved_roots: list[str] | None = None
        self._event_sink: Callable[[dict[str, Any]], None] | None = None
        self._watchdog_started = False
        self.spawned_total = 0

    @classmethod
    def get(cls) -> SessionManager:
        with cls._class_lock:
            if cls._instance is None:
                cls._instance = SessionManager()
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Test hook: drop the singleton (stops nothing)."""
        with cls._class_lock:
            cls._instance = None

    def configure(
        self,
        fleet_cfg: dict | None,
        approved_roots: list[str] | None = None,
        mcp_cfg: dict | None = None,
    ) -> None:
        with self._lock:
            merged = dict(_DEFAULTS)
            for k, v in (fleet_cfg or {}).items():
                if k == "worktrees" and isinstance(v, dict):
                    merged["worktrees"] = {**_DEFAULTS["worktrees"], **v}
                else:
                    merged[k] = v
            self._cfg = merged
            if approved_roots is not None:
                self._approved_roots = approved_roots
            if mcp_cfg is not None:
                self._mcp_cfg = mcp_cfg

    def _mcp_config_path(self) -> str | None:
        """Write a temp --mcp-config pointing Claude agents at Aether's /mcp."""
        mcp = self._mcp_cfg
        if not (mcp.get("enabled") and mcp.get("expose_to_fleet")):
            return None
        url = str(mcp.get("url") or "http://127.0.0.1:8765/mcp")
        server: dict[str, Any] = {"type": "http", "url": url}
        token = os.environ.get("AETHER_SIDECAR_TOKEN", "").strip()
        if token:
            server["headers"] = {"Authorization": f"Bearer {token}"}
        path = Path(tempfile.gettempdir()) / f"aether-mcp-{uuid.uuid4().hex[:8]}.json"
        path.write_text(json.dumps({"mcpServers": {"aether": server}}), encoding="utf-8")
        return str(path)

    def set_event_sink(self, fn: Callable[[dict[str, Any]], None] | None) -> None:
        with self._lock:
            self._event_sink = fn

    # ---- lifecycle ----

    def spawn(
        self,
        *,
        agent_type: str,
        prompt: str,
        workspace: str | None = None,
        label: str | None = None,
        isolate: bool | None = None,
        timeout_sec: int | None = None,
    ) -> AgentSession:
        if agent_type not in AGENT_TYPES:
            raise ValueError(f"unknown agent_type {agent_type!r}; one of {', '.join(AGENT_TYPES)}")
        cfg = self._cfg
        # Fork-bomb guard: if THIS process was itself spawned as a fleet agent,
        # AETHER_SPAWN_DEPTH is set — cap how deep the spawn chain can nest.
        depth = 0
        try:
            depth = int(os.environ.get("AETHER_SPAWN_DEPTH", "0"))
        except ValueError:
            depth = 0
        max_depth = int(cfg.get("max_spawn_depth", 2))
        if depth >= max_depth:
            raise ValueError(
                f"spawn depth {depth} ≥ max_spawn_depth {max_depth} — refusing "
                "to spawn (agent fork-bomb guard)"
            )
        with self._lock:
            active = sum(1 for s in self._sessions.values() if s.is_active())
            if active >= int(cfg["max_sessions"]):
                raise ValueError(
                    f"fleet is full ({active}/{cfg['max_sessions']} active) — "
                    "stop a session first or raise fleet.max_sessions"
                )

        cwd, err = _resolve_workspace(workspace, self._approved_roots)
        if err:
            raise ValueError(err)

        session_id = uuid.uuid4().hex[:8]
        wt_cfg = cfg.get("worktrees") or {}
        want_isolation = bool(wt_cfg.get("enabled", True)) if isolate is None else bool(isolate)
        branch = warning = None
        run_dir = cwd
        if want_isolation and agent_type != "terminal":
            run_dir, branch, warning = wt.provision(cwd, session_id)
            if warning and bool(cfg.get("require_isolation")):
                raise ValueError(f"isolation required but unavailable: {warning}")

        session = self._build_session(
            agent_type=agent_type,
            session_id=session_id,
            label=label or f"{agent_type}-{session_id[:4]}",
            prompt=prompt,
            workspace=str(run_dir),
            timeout_sec=float(timeout_sec or cfg["session_timeout_sec"]),
        )
        session.worktree_branch = branch
        session.worktree_dir = str(run_dir) if branch else None
        session._on_event = self._dispatch_event

        with self._lock:
            self._sessions[session_id] = session
            self.spawned_total += 1
            self._ensure_watchdog()

        AuditLog.get().record(
            "agent_spawn",
            summary=f"{agent_type} [{session.label}]: {prompt[:200]}",
            extra={"session_id": session_id, "workspace": str(run_dir), "branch": branch},
        )
        if warning:
            session._emit("stderr", warning)
        try:
            session.start()
        except Exception as e:  # noqa: BLE001 — missing binary etc: don't leak a zombie
            session._emit("stderr", f"failed to start: {e}")
            session._set_state("error")
            self._cleanup_worktree(session)
        return session

    def _build_session(self, *, agent_type: str, **kw) -> AgentSession:
        cfg = self._cfg
        common = dict(
            agent_type=agent_type,
            env_allowlist=list(cfg.get("env_allowlist") or []),
            buffer_lines=int(cfg["output_buffer_lines"]),
            cost_cap_usd=float(cfg["cost_cap_usd"]),
            **kw,
        )
        if agent_type == "claude":
            mode = str(cfg["claude_permission_mode"])
            if mode == "bypassPermissions" and not bool(cfg.get("allow_bypass_permissions")):
                mode = "acceptEdits"
            return ClaudeCodeSession(
                permission_mode=mode,
                allowed_tools=list(cfg.get("claude_allowed_tools") or []),
                mcp_config=self._mcp_config_path(),
                **common,
            )
        if agent_type in ("codex", "opencode", "cursor"):
            return HeadlessSession(**common)
        return PTYSession(idle_detect_sec=float(cfg["idle_detect_sec"]), **common)

    def send(self, id_or_label: str, text: str) -> bool:
        session = self.resolve(id_or_label)
        if session is None:
            raise ValueError(f"no session matching {id_or_label!r}")
        ok = session.send(text)
        if ok:
            AuditLog.get().record(
                "agent_send",
                summary=text[:200],
                extra={"session_id": session.session_id},
            )
        return ok

    def stop(self, id_or_label: str) -> AgentSession:
        session = self.resolve(id_or_label)
        if session is None:
            raise ValueError(f"no session matching {id_or_label!r}")
        session.stop()
        self._cleanup_worktree(session)
        AuditLog.get().record("agent_stop", extra={"session_id": session.session_id})
        return session

    def stop_all(self, grace_sec: float = 5.0) -> int:
        with self._lock:
            active = [s for s in self._sessions.values() if s.is_active()]
        for s in active:
            s.stop(grace_sec=grace_sec)
            self._cleanup_worktree(s)
        if active:
            AuditLog.get().record("agent_stop_all", extra={"count": len(active)})
        return len(active)

    def _cleanup_worktree(self, session: AgentSession) -> None:
        keep = bool((self._cfg.get("worktrees") or {}).get("keep", True))
        wt.cleanup(Path(session.workspace), session.worktree_dir, keep=keep)

    # ---- queries ----

    def resolve(self, id_or_label: str) -> AgentSession | None:
        with self._lock:
            s = self._sessions.get(id_or_label)
            if s:
                return s
            for sess in self._sessions.values():
                if sess.label == id_or_label or sess.session_id.startswith(id_or_label):
                    return sess
        return None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [s.status_dict() for s in self._sessions.values()]

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._sessions.values() if s.is_active())

    def total_cost(self) -> float:
        with self._lock:
            return round(sum(s.cost_usd for s in self._sessions.values()), 6)

    def summary_line(self) -> str:
        """Compact fleet state for the orchestrator system prompt. '' when idle."""
        with self._lock:
            live = [s for s in self._sessions.values() if s.is_active()]
            if not live:
                return ""
            parts = []
            for s in live:
                mins = int((time.time() - s.created_at) / 60)
                parts.append(
                    f"[{s.session_id[:4]}] {s.agent_type} \"{s.label}\" "
                    f"{s.state} {mins}m ${s.cost_usd:.2f}"
                )
            return f"AGENT FLEET: {len(live)} active — " + "; ".join(parts)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            by_agent: dict[str, float] = {}
            for s in self._sessions.values():
                by_agent[s.agent_type] = round(by_agent.get(s.agent_type, 0.0) + s.cost_usd, 6)
            return {
                "active_sessions": sum(1 for s in self._sessions.values() if s.is_active()),
                "spawned_total": self.spawned_total,
                "cost_usd_by_agent": by_agent,
                "total_cost_usd": self.total_cost(),
            }

    # ---- watchdog & events ----

    def _ensure_watchdog(self) -> None:
        if self._watchdog_started:
            return
        self._watchdog_started = True
        threading.Thread(target=self._watchdog, daemon=True, name="fleet-watchdog").start()

    def _watchdog(self) -> None:
        while True:
            time.sleep(2.0)
            self._check_sessions()

    def _check_sessions(self, now: float | None = None) -> None:
        """One watchdog tick: enforce per-session timeout and cost cap."""
        with self._lock:
            sessions = list(self._sessions.values())
        now = now or time.time()
        for s in sessions:
                if not s.is_active():
                    continue
                if s.timeout_sec and now - s.created_at > s.timeout_sec:
                    s._emit("stderr", f"timeout after {int(s.timeout_sec)}s")
                    s.stop(final_state="timeout")
                    self._cleanup_worktree(s)
                elif s.cost_cap_usd and s.cost_usd > s.cost_cap_usd:
                    s._emit("stderr", f"cost cap ${s.cost_cap_usd:.2f} exceeded")
                    s.stop()
                    self._cleanup_worktree(s)

    def _dispatch_event(self, session: AgentSession, event: OutputEvent) -> None:
        with self._lock:
            sink = self._event_sink
        if sink is None:
            return
        payload: dict[str, Any] = {
            "type": "fleet",
            "session_id": session.session_id,
            "label": session.label,
            "agent_type": session.agent_type,
            "state": session.state,
            "event": event.kind,
            "content": event.content[:500],
            "seq": event.seq,
        }
        try:
            sink(payload)
        except Exception:  # noqa: BLE001
            pass
