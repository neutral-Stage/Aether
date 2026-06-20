"""Unit tests for MCP SSE transport (Phase 10, mocked HTTP)."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from aether.tools.mcp_client import MCPClient, MCPServerConfig
from aether.tools.mcp_client_sse import MCPSSEServerConfig, MCPSSESession, _parse_sse_events


@pytest.mark.unit
class TestMCPSSEParsing:
    def test_parse_sse_events(self) -> None:
        raw = "event: endpoint\ndata: /messages?session=abc\n\n"
        events, remainder = _parse_sse_events(raw)
        assert remainder == ""
        assert len(events) == 1
        assert events[0][0] == "endpoint"
        assert events[0][1] == "/messages?session=abc"


@pytest.mark.unit
class TestMCPServerConfig:
    def test_auto_detect_sse_from_url(self) -> None:
        cfg = MCPServerConfig.from_dict({
            "name": "remote",
            "url": "https://mcp.example.com",
            "enabled": True,
        })
        assert cfg.transport == "sse"
        assert cfg.is_valid()

    def test_auto_detect_stdio_from_command(self) -> None:
        cfg = MCPServerConfig.from_dict({
            "name": "local",
            "command": "npx",
            "args": ["-y", "pkg"],
            "enabled": True,
        })
        assert cfg.transport == "stdio"
        assert cfg.is_valid()


@pytest.mark.unit
class TestMCPSSESession:
    @patch.object(MCPSSESession, "_connect_sse")
    @patch.object(MCPSSESession, "_notify")
    @patch.object(MCPSSESession, "_request")
    def test_sse_session_lists_tools(
        self,
        mock_request: MagicMock,
        mock_notify: MagicMock,
        mock_connect: MagicMock,
    ) -> None:
        mock_request.side_effect = [
            {"protocolVersion": "2024-11-05", "capabilities": {}},
            {"tools": [{"name": "ping", "description": "ping", "inputSchema": {"type": "object"}}]},
        ]
        cfg = MCPSSEServerConfig(
            name="test",
            url="https://mcp.example.com",
            enabled=True,
        )
        session = MCPSSESession(cfg)
        session._message_url = "https://mcp.example.com/messages"  # noqa: SLF001
        session.start()
        tools = session.tools()
        assert len(tools) == 1
        assert tools[0].name == "ping"
        session.close()


@pytest.mark.unit
class TestMCPClientIntegration:
    def test_server_status_sanitized(self) -> None:
        client = MCPClient({
            "enabled": True,
            "servers": [
                {
                    "name": "remote",
                    "transport": "sse",
                    "url": "https://mcp.example.com",
                    "enabled": True,
                },
            ],
        })
        status = client.server_status()
        assert len(status) == 1
        assert status[0]["transport"] == "sse"
        assert "url" in status[0]
        assert "headers" not in status[0]

    @patch.object(MCPSSESession, "start")
    @patch.object(MCPSSESession, "tools")
    def test_registers_sse_tools(self, mock_tools: MagicMock, mock_start: MagicMock) -> None:
        from aether.tools.mcp_client import MCPToolDescriptor

        mock_tools.return_value = [
            MCPToolDescriptor("remote", "search", "search docs", {"type": "object"}),
        ]
        client = MCPClient({
            "enabled": True,
            "servers": [{
                "name": "remote",
                "url": "https://mcp.example.com",
                "enabled": True,
            }],
        })
        registered: list[str] = []

        def _reg(**kwargs: Any) -> None:
            registered.append(kwargs["name"])

        count = client.register_with_registry(_reg)
        assert count == 1
        assert registered[0] == "mcp_remote_search"
