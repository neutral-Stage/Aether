"""Aether-as-MCP-server: JSON-RPC protocol + policy gating (no display needed)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aether.tools.registry import Registry, ToolSpec
import sidecar.mcp_server as mcp
from sidecar.server import app


def _echo_registry() -> Registry:
    reg = Registry()
    reg.register(ToolSpec(
        name="echo_test",
        description="echo back the message",
        json_schema={"type": "object", "properties": {"message": {"type": "string"}}},
        permission="none",
        impact="read",
        handler=lambda args, ctx: f"echo:{args.get('message', '')}",
    ))
    reg.register(ToolSpec(
        name="danger_test",
        description="a destructive tool",
        json_schema={"type": "object", "properties": {}},
        permission="shell",
        impact="destructive",
        handler=lambda args, ctx: "did something dangerous",
    ))
    reg.register(ToolSpec(
        name="secret_tool",
        description="needs a gated capability",
        json_schema={"type": "object", "properties": {}},
        permission="input",
        impact="read",
        handler=lambda args, ctx: "ok",
    ))
    return reg


@pytest.fixture
def client(monkeypatch):
    reg = _echo_registry()
    monkeypatch.setattr(mcp, "_registry", lambda: reg)
    # enable the endpoint + expose our test tools; gate the 'input' capability off
    cfg = {
        "mcp_server": {
            "enabled": True,
            "expose_tools": ["echo_test", "danger_test", "secret_tool"],
        },
        "capabilities": {"input": False},
        "policy": {"approved_file_roots": ["~"]},
    }

    class FakeConfig:
        def get(self, key, *a, **k):
            return cfg.get(key)

    monkeypatch.setattr(mcp, "load_config", lambda *a, **k: FakeConfig())

    class FakeAudit:
        @staticmethod
        def get():
            return FakeAudit()

        def record(self, *a, **k):
            return None

    monkeypatch.setattr(mcp, "AuditLog", FakeAudit)
    with TestClient(app) as c:
        yield c


def _rpc(client, method, params=None, req_id=1):
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body)


def test_initialize(client):
    r = _rpc(client, "initialize", {"protocolVersion": "2024-11-05"})
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "aether"
    assert "tools" in result["capabilities"]


def test_notification_returns_202(client):
    # no id => notification => no reply
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202


def test_tools_list(client):
    r = _rpc(client, "tools/list")
    tools = {t["name"] for t in r.json()["result"]["tools"]}
    assert tools == {"echo_test", "danger_test", "secret_tool"}
    echo = next(t for t in r.json()["result"]["tools"] if t["name"] == "echo_test")
    assert "inputSchema" in echo


def test_tools_call_success(client):
    r = _rpc(client, "tools/call", {"name": "echo_test", "arguments": {"message": "hi"}})
    result = r.json()["result"]
    assert result["content"][0]["text"] == "echo:hi"
    assert not result.get("isError")


def test_tools_call_blocks_destructive(client):
    r = _rpc(client, "tools/call", {"name": "danger_test", "arguments": {}})
    result = r.json()["result"]
    assert result["isError"] is True
    assert "destructive" in result["content"][0]["text"]


def test_tools_call_blocks_disabled_capability(client):
    r = _rpc(client, "tools/call", {"name": "secret_tool", "arguments": {}})
    result = r.json()["result"]
    assert result["isError"] is True
    assert "capability" in result["content"][0]["text"]


def test_tools_call_rejects_unexposed_tool(client):
    r = _rpc(client, "tools/call", {"name": "not_a_tool", "arguments": {}})
    result = r.json()["result"]
    assert result["isError"] is True
    assert "not exposed" in result["content"][0]["text"]


def test_unknown_method(client):
    r = _rpc(client, "tools/frobnicate")
    assert r.json()["error"]["code"] == -32601


def test_disabled_returns_404(monkeypatch):
    class FakeConfig:
        def get(self, key, *a, **k):
            return {"enabled": False} if key == "mcp_server" else None

    monkeypatch.setattr(mcp, "load_config", lambda *a, **k: FakeConfig())
    with TestClient(app) as c:
        r = _rpc(c, "initialize")
        assert r.status_code == 404
