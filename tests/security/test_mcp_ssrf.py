"""MCP SSE connect-time SSRF blocking (P1 hardening)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aether.core.url_safety import URLSafetyError, validate_outbound_url
from aether.tools.mcp_client import MCPClient
from aether.tools.mcp_client_sse import MCPSSEServerConfig, MCPSSESession


@pytest.mark.security
class TestURLSafety:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8080/sse",
            "http://localhost/sse",
            "http://10.0.0.1/mcp",
            "http://192.168.1.50/sse",
            "http://172.16.0.1/sse",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/sse",
            "file:///etc/passwd",
            "gopher://127.0.0.1",
        ],
    )
    def test_blocks_private_and_non_http_urls(self, url: str) -> None:
        with pytest.raises(URLSafetyError):
            validate_outbound_url(url, allow_private=False)

    def test_allows_public_https(self) -> None:
        validate_outbound_url("https://mcp.example.com/sse", allow_private=False)

    def test_allow_private_opt_in(self) -> None:
        validate_outbound_url("http://127.0.0.1:8765/sse", allow_private=True)

    @patch("aether.core.url_safety.socket.getaddrinfo")
    def test_blocks_dns_to_private_ip(self, mock_getaddrinfo: MagicMock) -> None:
        mock_getaddrinfo.return_value = [
            (None, None, None, None, ("192.168.0.99", 443)),
        ]
        with pytest.raises(URLSafetyError, match="resolves to blocked"):
            validate_outbound_url("https://evil.example.com/sse", allow_private=False)


@pytest.mark.security
class TestMCPSSEConnectBlocking:
    def test_start_rejects_private_sse_url(self) -> None:
        cfg = MCPSSEServerConfig(
            name="local",
            url="http://127.0.0.1:8080",
            enabled=True,
        )
        session = MCPSSESession(cfg)
        with pytest.raises(RuntimeError, match="blocked URL"):
            session.start()

    def test_mcp_client_sse_session_not_created_for_private_url(self) -> None:
        client = MCPClient({
            "enabled": True,
            "allow_private_urls": False,
            "servers": [{
                "name": "metadata",
                "url": "http://169.254.169.254",
                "enabled": True,
            }],
        })
        tools = client.list_tools()
        assert tools == []
        status = client.server_status()
        assert status[0]["status"] == "error"

    @patch.object(MCPSSESession, "_connect_sse")
    @patch.object(MCPSSESession, "_notify")
    @patch.object(MCPSSESession, "_request")
    def test_endpoint_redirect_validated(
        self,
        mock_request: MagicMock,
        mock_notify: MagicMock,
        mock_connect: MagicMock,
    ) -> None:
        cfg = MCPSSEServerConfig(
            name="remote",
            url="https://mcp.example.com",
            enabled=True,
        )
        session = MCPSSESession(cfg)
        session._message_url = "https://mcp.example.com/messages"  # noqa: SLF001
        with pytest.raises(RuntimeError, match="blocked URL"):
            session._handle_sse_event("endpoint", "http://127.0.0.1/post")  # noqa: SLF001
