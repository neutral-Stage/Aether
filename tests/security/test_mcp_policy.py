"""MCP tool impact mapping and subprocess env hardening (P1)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aether.tools.delegation import build_subprocess_env
from aether.tools.mcp_client import MCPClient, mcp_tool_impact
from aether.tools.mcp_client import MCPToolDescriptor as StdioDescriptor


@pytest.mark.security
class TestMCPToolImpact:
    def test_read_only_hint_maps_to_read(self) -> None:
        assert mcp_tool_impact({"readOnlyHint": True}) == "read"

    def test_destructive_hint_maps_to_destructive(self) -> None:
        assert mcp_tool_impact({"destructiveHint": True}) == "destructive"

    def test_unknown_tool_defaults_destructive(self) -> None:
        assert mcp_tool_impact({}) == "destructive"
        assert mcp_tool_impact(None, careful=True) == "destructive"

    @patch.object(MCPClient, "list_tools")
    def test_register_uses_annotation_impact(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [
            StdioDescriptor(
                "srv",
                "safe_read",
                "read docs",
                {"type": "object"},
                annotations={"readOnlyHint": True},
            ),
            StdioDescriptor(
                "srv",
                "danger",
                "delete things",
                {"type": "object"},
                annotations={"destructiveHint": True},
            ),
        ]
        client = MCPClient({"enabled": True})
        impacts: dict[str, str] = {}

        def _reg(**kwargs) -> None:
            impacts[kwargs["name"]] = kwargs["impact"]

        client.register_with_registry(_reg)
        assert impacts["mcp_srv_safe_read"] == "read"
        assert impacts["mcp_srv_danger"] == "destructive"


@pytest.mark.security
class TestMCPStdioEnv:
    def test_stdio_env_strips_secrets(self) -> None:
        with patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": "sk-secret", "PATH": "/usr/bin"},
            clear=False,
        ):
            env = build_subprocess_env(["CUSTOM_VAR"])
            env["CUSTOM_VAR"] = "configured"
        assert "ANTHROPIC_API_KEY" not in env
        assert env["PATH"] == "/usr/bin"
        assert env["CUSTOM_VAR"] == "configured"
