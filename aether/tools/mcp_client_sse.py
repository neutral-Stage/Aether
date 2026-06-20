"""MCP SSE/HTTP transport client (Phase 10, FR-20).

Implements the Model Context Protocol HTTP+SSE transport (protocol 2024-11-05):
  1. GET the SSE endpoint — receive an `endpoint` event with the message POST URL.
  2. POST JSON-RPC requests to that URL.
  3. Receive JSON-RPC responses on the SSE stream.

Spec: https://spec.modelcontextprotocol.io/
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from aether import __version__ as AETHER_VERSION

from ..core.url_safety import URLSafetyError, validate_outbound_url

log = logging.getLogger(__name__)

_JSONRPC_VERSION = "2.0"
_PROTOCOL_VERSION = "2024-11-05"
_DEFAULT_TIMEOUT = 30.0


@dataclass
class MCPSSEServerConfig:
    name: str
    url: str
    enabled: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    sse_path: str = "/sse"
    timeout_seconds: float = _DEFAULT_TIMEOUT
    allow_private_urls: bool = False


@dataclass
class MCPToolDescriptor:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict)


def _parse_sse_events(buffer: str) -> tuple[list[tuple[str | None, str]], str]:
    """Parse complete SSE events from a text buffer; return remainder."""
    events: list[tuple[str | None, str]] = []
    while "\n\n" in buffer:
        block, buffer = buffer.split("\n\n", 1)
        event_type: str | None = None
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            events.append((event_type, "\n".join(data_lines)))
    return events, buffer


class MCPSSESession:
    """MCP session over HTTP+SSE for one remote server."""

    def __init__(self, cfg: MCPSSEServerConfig, *, http_client: httpx.Client | None = None):
        self.cfg = cfg
        self._client = http_client
        self._owns_client = http_client is None
        self._lock = threading.Lock()
        self._message_url: str | None = None
        self._sse_response: httpx.Response | None = None
        self._sse_thread: threading.Thread | None = None
        self._pending: dict[int | str, dict[str, Any]] = {}
        self._waiters: dict[int | str, threading.Event] = {}
        self._tools: list[MCPToolDescriptor] = []
        self._initialized = False
        self._next_id = 1
        self._stop = threading.Event()

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.cfg.timeout_seconds,
                headers=dict(self.cfg.headers),
            )
        return self._client

    def _sse_url(self) -> str:
        base = self.cfg.url.rstrip("/")
        path = self.cfg.sse_path if self.cfg.sse_path.startswith("/") else f"/{self.cfg.sse_path}"
        if path.startswith("http"):
            return path
        return f"{base}{path}"

    def _validate_url(self, url: str) -> None:
        try:
            validate_outbound_url(url, allow_private=self.cfg.allow_private_urls)
        except URLSafetyError as exc:
            raise RuntimeError(
                f"MCP SSE {self.cfg.name}: blocked URL ({exc})"
            ) from exc

    def start(self) -> None:
        if self._initialized:
            return
        self._validate_url(self._sse_url())
        self._connect_sse()
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
                input_schema=dict(
                    tool.get("inputSchema") or {"type": "object", "properties": {}}
                ),
                annotations=dict(tool.get("annotations") or {}),
            ))
        self._initialized = True
        log.info("MCP SSE %s: %d tools", self.cfg.name, len(self._tools))

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
        self._stop.set()
        if self._sse_thread and self._sse_thread.is_alive():
            self._sse_thread.join(timeout=2.0)
        if self._sse_response is not None:
            try:
                self._sse_response.close()
            except Exception:  # noqa: BLE001
                pass
        self._sse_response = None
        if self._owns_client and self._client is not None:
            self._client.close()
        self._client = None
        self._initialized = False
        self._message_url = None

    @property
    def connected(self) -> bool:
        return self._initialized and self._message_url is not None

    def _connect_sse(self) -> None:
        client = self._get_client()
        sse_url = self._sse_url()
        log.info("MCP SSE connecting %s at %s", self.cfg.name, sse_url)
        ready = threading.Event()

        def _reader() -> None:
            try:
                with client.stream("GET", sse_url, headers={"Accept": "text/event-stream"}) as resp:
                    resp.raise_for_status()
                    self._sse_response = resp
                    buffer = ""
                    for chunk in resp.iter_text():
                        if self._stop.is_set():
                            break
                        buffer += chunk
                        events, buffer = _parse_sse_events(buffer)
                        for event_type, data in events:
                            self._handle_sse_event(event_type, data)
                            if event_type == "endpoint" and self._message_url:
                                ready.set()
            except Exception as exc:  # noqa: BLE001
                log.warning("MCP SSE stream ended for %s: %s", self.cfg.name, exc)
            finally:
                ready.set()

        self._sse_thread = threading.Thread(target=_reader, name=f"mcp-sse-{self.cfg.name}", daemon=True)
        self._sse_thread.start()
        if not ready.wait(timeout=self.cfg.timeout_seconds):
            raise TimeoutError(f"MCP SSE {self.cfg.name}: timed out waiting for endpoint event")
        if not self._message_url:
            raise RuntimeError(f"MCP SSE {self.cfg.name}: no message endpoint received")

    def _handle_sse_event(self, event_type: str | None, data: str) -> None:
        if event_type == "endpoint":
            endpoint = data.strip()
            if endpoint.startswith("/"):
                parsed = urlparse(self._sse_url())
                origin = f"{parsed.scheme}://{parsed.netloc}"
                message_url = urljoin(origin, endpoint)
            else:
                message_url = endpoint
            self._validate_url(message_url)
            self._message_url = message_url
            log.debug("MCP SSE %s message URL: %s", self.cfg.name, self._message_url)
            return
        if event_type in (None, "message"):
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                log.debug("MCP SSE non-JSON event: %s", data[:120])
                return
            req_id = msg.get("id")
            if req_id is not None and req_id in self._waiters:
                self._pending[req_id] = msg
                self._waiters[req_id].set()

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        payload = {"jsonrpc": _JSONRPC_VERSION, "method": method, "params": params}
        self._post_message(payload)

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        req_id = self._next_id
        self._next_id += 1
        payload = {
            "jsonrpc": _JSONRPC_VERSION,
            "id": req_id,
            "method": method,
            "params": params,
        }
        waiter = threading.Event()
        with self._lock:
            self._waiters[req_id] = waiter
        self._post_message(payload)
        if not waiter.wait(timeout=self.cfg.timeout_seconds):
            with self._lock:
                self._waiters.pop(req_id, None)
                self._pending.pop(req_id, None)
            raise TimeoutError(f"MCP SSE {self.cfg.name} timed out on {method}")
        with self._lock:
            msg = self._pending.pop(req_id, {})
            self._waiters.pop(req_id, None)
        if "error" in msg:
            err = msg["error"]
            raise RuntimeError(f"MCP error {err.get('code')}: {err.get('message')}")
        return msg.get("result") or {}

    def _post_message(self, payload: dict[str, Any]) -> None:
        if not self._message_url:
            raise RuntimeError(f"MCP SSE {self.cfg.name}: message endpoint not ready")
        client = self._get_client()
        resp = client.post(self._message_url, json=payload)
        resp.raise_for_status()
        # Some servers return the JSON-RPC response inline on POST
        if resp.content:
            try:
                msg = resp.json()
            except json.JSONDecodeError:
                return
            req_id = msg.get("id")
            if req_id is not None and req_id in self._waiters:
                self._pending[req_id] = msg
                self._waiters[req_id].set()


class MCPSSEClient:
    """Manage MCP SSE sessions (mirrors MCPSSESession for registry integration)."""

    def __init__(self, cfg: MCPSSEServerConfig):
        self.cfg = cfg
        self._session: MCPSSESession | None = None

    def connect(self) -> bool:
        try:
            self._session = MCPSSESession(self.cfg)
            self._session.start()
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("MCP SSE connect failed for %s: %s", self.cfg.name, exc)
            return False

    def list_tools(self) -> list[dict[str, Any]]:
        if self._session is None:
            if not self.connect():
                return []
        assert self._session is not None
        return [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.input_schema,
            }
            for t in self._session.tools()
        ]

    @property
    def connected(self) -> bool:
        return self._session is not None and self._session.connected

    def close(self) -> None:
        if self._session:
            self._session.close()
        self._session = None
