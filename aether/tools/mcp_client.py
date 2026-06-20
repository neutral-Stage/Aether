"""MCP client — stdio and SSE server connections (§6.5, FR-20).

Connects to configured MCP servers over stdio JSON-RPC or HTTP+SSE, lists tools,
and registers them into the agent tool registry. Disabled by default in config.

Transport auto-detection:
  * `url` set (or transport: sse) → HTTP+SSE
  * `command` set (or transport: stdio) → subprocess stdio
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from aether import __version__ as AETHER_VERSION

from .delegation import build_subprocess_env
from .mcp_client_sse import MCPSSEServerConfig, MCPSSESession

log = logging.getLogger(__name__)

_JSONRPC_VERSION = "2.0"
_PROTOCOL_VERSION = "2024-11-05"

TransportKind = Literal["stdio", "sse"]


@dataclass
class MCPServerConfig:
    name: str
    enabled: bool = False
    transport: TransportKind = "stdio"
    # stdio
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # sse
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    sse_path: str = "/sse"
    timeout_seconds: float = 30.0

    @classmethod
    def from_dict(cls, entry: dict[str, Any]) -> "MCPServerConfig":
        name = str(entry.get("name", "unnamed"))
        url = str(entry.get("url") or "").strip()
        command = str(entry.get("command") or "").strip()
        transport_raw = str(entry.get("transport") or "").lower()
        if transport_raw in {"stdio", "sse"}:
            transport: TransportKind = transport_raw  # type: ignore[assignment]
        elif url:
            transport = "sse"
        else:
            transport = "stdio"
        return cls(
            name=name,
            enabled=bool(entry.get("enabled", False)),
            transport=transport,
            command=command,
            args=list(entry.get("args") or []),
            env=dict(entry.get("env") or {}),
            url=url,
            headers=dict(entry.get("headers") or {}),
            sse_path=str(entry.get("sse_path") or "/sse"),
            timeout_seconds=float(entry.get("timeout_seconds", 30.0)),
        )

    def is_valid(self) -> bool:
        if not self.enabled:
            return False
        if self.transport == "sse":
            return bool(self.url)
        return bool(self.command)


@dataclass
class MCPToolDescriptor:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict)


def mcp_tool_impact(
    annotations: dict[str, Any] | None,
    *,
    careful: bool = False,
) -> str:
    """Map MCP tool annotations to registry impact level.

  * ``readOnlyHint: true`` → ``read``
  * ``destructiveHint: true`` → ``destructive``
  * otherwise → ``destructive`` (unknown tools are not treated as safe)
    """
    ann = annotations or {}
    if ann.get("readOnlyHint") is True:
        return "read"
    if ann.get("destructiveHint") is True:
        return "destructive"
    if careful:
        return "destructive"
    return "destructive"


class _StdioSession:
    """Minimal MCP stdio JSON-RPC session for one server process."""

    def __init__(self, cfg: MCPServerConfig):
        self.cfg = cfg
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._tools: list[MCPToolDescriptor] = []
        self._initialized = False

    def start(self) -> None:
        if self._proc is not None:
            return
        # Minimal inherited env: safe PATH/HOME defaults + explicit MCP env only.
        env = build_subprocess_env(list(self.cfg.env.keys()))
        env.update(self.cfg.env)
        cmd = [self.cfg.command, *self.cfg.args]
        log.info("MCP stdio starting %s: %s", self.cfg.name, cmd)
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self._request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "aether", "version": AETHER_VERSION},
        })
        self._notify("notifications/initialized", {})
        result = self._request("tools/list", {})
        for tool in result.get("tools") or []:
            self._tools.append(MCPToolDescriptor(
                server=self.cfg.name,
                name=str(tool.get("name", "")),
                description=str(tool.get("description", "")),
                input_schema=dict(tool.get("inputSchema") or {"type": "object", "properties": {}}),
                annotations=dict(tool.get("annotations") or {}),
            ))
        self._initialized = True
        log.info("MCP stdio %s: %d tools", self.cfg.name, len(self._tools))

    def tools(self) -> list[MCPToolDescriptor]:
        if not self._initialized:
            self.start()
        return list(self._tools)

    def call(self, tool: str, args: dict[str, Any]) -> str:
        if not self._initialized:
            self.start()
        result = self._request("tools/call", {"name": tool, "arguments": args})
        content = result.get("content") or []
        texts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(str(block.get("text", "")))
        if texts:
            return "\n".join(texts)
        if result.get("isError"):
            return f"MCP error: {json.dumps(result)[:500]}"
        return json.dumps(result)[:2000]

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                self._proc.kill()
        self._proc = None
        self._initialized = False

    @property
    def connected(self) -> bool:
        return self._initialized and self._proc is not None and self._proc.poll() is None

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": _JSONRPC_VERSION, "method": method, "params": params})

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        req_id = self._next_id
        self._next_id += 1
        self._write({
            "jsonrpc": _JSONRPC_VERSION,
            "id": req_id,
            "method": method,
            "params": params,
        })
        return self._read_response(req_id)

    def _write(self, payload: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            raise RuntimeError(f"MCP process not running: {self.cfg.name}")
        line = json.dumps(payload, separators=(",", ":"))
        with self._lock:
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()

    def _read_response(self, req_id: int) -> dict[str, Any]:
        if not self._proc or not self._proc.stdout:
            raise RuntimeError(f"MCP process not running: {self.cfg.name}")
        deadline = time.time() + self.cfg.timeout_seconds
        while time.time() < deadline:
            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError(f"MCP {self.cfg.name} closed stdout")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log.debug("MCP non-JSON line: %s", line[:120])
                continue
            if msg.get("id") == req_id:
                if "error" in msg:
                    err = msg["error"]
                    raise RuntimeError(f"MCP error {err.get('code')}: {err.get('message')}")
                return msg.get("result") or {}
        raise TimeoutError(f"MCP {self.cfg.name} timed out waiting for {req_id}")


# Module-level holder for sidecar reload (Phase 10)
_active_mcp_client: "MCPClient | None" = None
_mcp_lock = threading.Lock()


def get_active_mcp_client() -> "MCPClient | None":
    with _mcp_lock:
        return _active_mcp_client


def set_active_mcp_client(client: "MCPClient | None") -> None:
    global _active_mcp_client
    to_close: MCPClient | None = None
    with _mcp_lock:
        if _active_mcp_client is not None and _active_mcp_client is not client:
            to_close = _active_mcp_client
        _active_mcp_client = client
    if to_close is not None:
        to_close.close()


class MCPClient:
    """MCP client managing multiple stdio and SSE server sessions."""

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.allow_private_urls = bool(cfg.get("allow_private_urls", False))
        self._careful = bool(cfg.get("careful", False))
        self._servers: list[MCPServerConfig] = []
        for entry in cfg.get("servers") or []:
            self._servers.append(MCPServerConfig.from_dict(entry))
        self._sessions: dict[str, _StdioSession | MCPSSESession] = {}
        self._session_lock = threading.Lock()
        self._connection_status: dict[str, str] = {}

    @property
    def servers(self) -> list[MCPServerConfig]:
        return [s for s in self._servers if s.is_valid()]

    def server_status(self) -> list[dict[str, Any]]:
        """Sanitized status for sidecar / Swift settings UI."""
        out: list[dict[str, Any]] = []
        for srv in self._servers:
            status = self._connection_status.get(srv.name, "disabled")
            if srv.enabled and srv.is_valid():
                session = self._sessions.get(srv.name)
                if session and getattr(session, "connected", False):
                    status = "connected"
                elif status not in {"connected", "error"}:
                    status = "pending"
            elif not srv.enabled:
                status = "disabled"
            entry: dict[str, Any] = {
                "name": srv.name,
                "enabled": srv.enabled,
                "transport": srv.transport,
                "status": status,
            }
            if srv.transport == "sse":
                entry["url"] = srv.url
            else:
                entry["command"] = srv.command
                entry["args"] = srv.args
            out.append(entry)
        return out

    def _session(self, server: str) -> _StdioSession | MCPSSESession | None:
        cfg = next((s for s in self.servers if s.name == server), None)
        if cfg is None:
            return None
        with self._session_lock:
            if server not in self._sessions:
                try:
                    if cfg.transport == "sse":
                        sse_cfg = MCPSSEServerConfig(
                            name=cfg.name,
                            url=cfg.url,
                            enabled=cfg.enabled,
                            headers=cfg.headers,
                            sse_path=cfg.sse_path,
                            timeout_seconds=cfg.timeout_seconds,
                            allow_private_urls=self.allow_private_urls,
                        )
                        self._sessions[server] = MCPSSESession(sse_cfg)
                    else:
                        self._sessions[server] = _StdioSession(cfg)
                    self._connection_status[server] = "connected"
                except Exception as exc:  # noqa: BLE001
                    log.warning("MCP %s connect failed: %s", server, exc)
                    self._connection_status[server] = "error"
                    return None
            return self._sessions[server]

    def list_tools(self) -> list[MCPToolDescriptor]:
        if not self.enabled:
            return []
        tools: list[MCPToolDescriptor] = []
        for srv in self.servers:
            try:
                session = self._session(srv.name)
                if session:
                    tools.extend(session.tools())
            except Exception as exc:  # noqa: BLE001
                log.warning("MCP %s list_tools failed: %s", srv.name, exc)
                self._connection_status[srv.name] = "error"
        return tools

    def invoke(self, server: str, tool: str, args: dict[str, Any]) -> str:
        if not self.enabled:
            return "MCP is disabled in config."
        session = self._session(server)
        if session is None:
            return f"MCP server not found or disabled: {server}"
        try:
            return session.call(tool, args)
        except Exception as exc:  # noqa: BLE001
            log.exception("MCP invoke %s.%s failed", server, tool)
            self._connection_status[server] = "error"
            return f"MCP invoke error: {exc}"

    def register_with_registry(self, register_fn: Callable[..., None]) -> int:
        """Register MCP tools into the agent registry. Returns count registered."""
        if not self.enabled:
            return 0
        set_active_mcp_client(self)
        count = 0
        for desc in self.list_tools():
            reg_name = f"mcp_{desc.server}_{desc.name}".replace("-", "_")
            server, tool = desc.server, desc.name

            def _make_handler(srv: str, tname: str) -> Callable[[dict, Any], str]:
                def _handler(args: dict, _ctx: Any) -> str:
                    return self.invoke(srv, tname, args)
                return _handler

            register_fn(
                name=reg_name,
                description=f"[MCP:{desc.server}] {desc.description or desc.name}",
                json_schema=desc.input_schema,
                permission="network",
                impact=mcp_tool_impact(desc.annotations, careful=self._careful),
                handler=_make_handler(server, tool),
            )
            count += 1
            log.info("Registered MCP tool %s", reg_name)
        return count

    def reload(self) -> dict[str, Any]:
        """Close sessions; next tool use reconnects. Returns status summary."""
        self.close()
        self._connection_status.clear()
        return {"status": "reloaded", "servers": self.server_status()}

    def close(self) -> None:
        with self._session_lock:
            for session in self._sessions.values():
                session.close()
            self._sessions.clear()
        global _active_mcp_client
        with _mcp_lock:
            if _active_mcp_client is self:
                _active_mcp_client = None
