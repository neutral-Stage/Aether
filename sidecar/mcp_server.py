"""Aether as an MCP server — expose a curated tool subset to fleet agents.

A spawned Claude Code (or any MCP client) points `--mcp-config` at
`POST /mcp` and can then perceive and drive the screen *through Aether*, so
the policy gate + audit log apply to everything a fleet agent does to the GUI.

Minimal streamable-HTTP JSON-RPC 2.0 (protocol 2024-11-05), matching the
transport the in-repo MCP client already speaks (aether/tools/mcp_client_sse.py).
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from aether.core.config import load_config
from aether.core.audit_log import AuditLog
from aether.core.policy import Policy, PolicyConfig, normalize_file_roots
from aether.tools.registry import DEFAULT_REGISTRY, AgentContext

from .auth import require_auth

router = APIRouter(tags=["mcp"])

_PROTOCOL_VERSION = "2024-11-05"
_JSONRPC = "2.0"
_ROOT = Path(__file__).resolve().parents[1]
try:
    _VERSION = (_ROOT / "VERSION").read_text(encoding="utf-8").strip()
except OSError:
    _VERSION = "0"

# Perception + control tools safe to expose to agents. All read/reversible;
# destructive-impact calls are blocked per-request regardless (belt + braces).
DEFAULT_EXPOSE_TOOLS = [
    "get_screen_context", "screenshot", "analyze_screen",
    "click", "type_text", "open_app",
]

# One shared context so click-by-element_index sees the elements a prior
# get_screen_context populated. ponytail: single shared ctx — fine for a
# localhost single-user sidecar; per-session ctx if concurrent agents clash.
_ctx = AgentContext()
_dispatch_lock = threading.Lock()


def _mcp_cfg() -> dict[str, Any]:
    return load_config(validate=False).get("mcp_server") or {}


def _registry():
    return DEFAULT_REGISTRY


def _allowed_names(cfg: dict) -> list[str]:
    names = cfg.get("expose_tools") or DEFAULT_EXPOSE_TOOLS
    return [n for n in names if _registry().get(n) is not None]


def _build_policy() -> Policy:
    full = load_config(validate=False)
    caps = full.get("capabilities") or {}
    policy_raw = full.get("policy") or {}
    return Policy(PolicyConfig(
        capabilities=caps,
        approved_file_roots=normalize_file_roots(policy_raw.get("approved_file_roots")),
    ))


def _ok(req_id: Any, result: dict) -> dict:
    return {"jsonrpc": _JSONRPC, "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": _JSONRPC, "id": req_id, "error": {"code": code, "message": message}}


def _tool_list(cfg: dict) -> dict:
    tools = []
    for name in _allowed_names(cfg):
        spec = _registry().get(name)
        tools.append({
            "name": spec.name,
            "description": spec.description,
            "inputSchema": spec.json_schema,
        })
    return {"tools": tools}


def _tool_call(cfg: dict, params: dict) -> dict:
    name = params.get("name", "")
    args = params.get("arguments") or {}
    if name not in _allowed_names(cfg):
        return {"content": [{"type": "text", "text": f"tool not exposed: {name}"}],
                "isError": True}
    spec = _registry().get(name)
    policy = _build_policy()
    if not policy.allows_tool(spec):
        return {"content": [{"type": "text",
                             "text": f"blocked: capability '{spec.permission}' is disabled"}],
                "isError": True}
    if policy.impact_of(spec, args) == "destructive":
        return {"content": [{"type": "text",
                             "text": "blocked: destructive action not allowed via MCP"}],
                "isError": True}
    with _dispatch_lock:
        result = _registry().dispatch(name, args, _ctx)
    AuditLog.get().record("mcp_tool_call", tool=name, tool_args=args, summary=result[:200])
    is_error = result.startswith("ERROR") or result.startswith("blocked:")
    return {"content": [{"type": "text", "text": result}], "isError": is_error}


def _handle(method: str, req_id: Any, params: dict, cfg: dict) -> dict:
    if method == "initialize":
        return _ok(req_id, {
            "protocolVersion": params.get("protocolVersion", _PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "aether", "version": _VERSION},
        })
    if method == "ping":
        return _ok(req_id, {})
    if method == "tools/list":
        return _ok(req_id, _tool_list(cfg))
    if method == "tools/call":
        return _ok(req_id, _tool_call(cfg, params))
    return _err(req_id, -32601, f"method not found: {method}")


@router.post("/mcp")
async def mcp_endpoint(request: Request, _auth: None = Depends(require_auth)) -> Any:
    cfg = _mcp_cfg()
    if not bool(cfg.get("enabled", False)):
        raise HTTPException(404, "MCP server disabled (set mcp_server.enabled: true).")
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"invalid JSON: {exc}") from exc

    messages = body if isinstance(body, list) else [body]
    responses = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        method = msg.get("method", "")
        req_id = msg.get("id")
        params = msg.get("params") or {}
        if req_id is None:  # notification (e.g. notifications/initialized) — no reply
            continue
        responses.append(_handle(method, req_id, params, cfg))

    if not responses:
        return Response(status_code=202)  # notifications only
    return responses if isinstance(body, list) else responses[0]
