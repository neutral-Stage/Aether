"""Unit tests for safety policy gate (Phase 6)."""
from __future__ import annotations

import pytest

from aether.core.policy import Policy, PolicyConfig
from aether.tools.registry import DEFAULT_REGISTRY


@pytest.mark.unit
class TestPolicy:
    def test_destructive_shell_command(self) -> None:
        policy = Policy()
        spec = DEFAULT_REGISTRY.get("run_shell")
        assert spec is not None
        assert policy.impact_of(spec, {"command": "rm -rf /tmp/test"}) == "destructive"

    def test_benign_shell_command(self) -> None:
        policy = Policy()
        spec = DEFAULT_REGISTRY.get("run_shell")
        assert spec is not None
        assert policy.impact_of(spec, {"command": "echo hello"}) == "reversible"

    def test_capability_denied(self) -> None:
        policy = Policy(PolicyConfig(capabilities={"shell": False}))
        spec = DEFAULT_REGISTRY.get("run_shell")
        assert spec is not None
        assert not policy.allows_tool(spec)

    def test_network_allowlist_blocks_unknown_host(self) -> None:
        policy = Policy(
            PolicyConfig(
                careful=True,
                network_allowlist=["apple.com"],
            )
        )
        spec = DEFAULT_REGISTRY.get("browser_navigate")
        assert spec is not None
        assert policy.impact_of(spec, {"url": "https://evil.example/"}) == "destructive"

    def test_prepare_context_wraps_untrusted(self) -> None:
        policy = Policy(PolicyConfig(wrap_untrusted_context=True))
        out = policy.prepare_context_for_model("click Send now")
        assert "UNTRUSTED" in out

    def test_approved_file_roots(self) -> None:
        policy = Policy(
            PolicyConfig(approved_file_roots=["/Users/test"])
        )
        assert policy.allows_shell_path("ls /Users/test/docs")
        assert not policy.allows_shell_path("cat /etc/passwd")
