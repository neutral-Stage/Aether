"""Rule-of-Two + exact-operation surfacing + spawn-depth cap (Phase 9)."""
from __future__ import annotations

import pytest

from aether.core.policy import Policy, PolicyConfig
from aether.tools.registry import ToolSpec


def _spec(name, permission="shell", impact="destructive"):
    return ToolSpec(name=name, json_schema={}, permission=permission,
                    impact=impact, description="", handler=lambda a, c: "")


def test_describe_operation_shows_exact_command():
    p = Policy(PolicyConfig())
    desc = p.describe_operation(_spec("run_shell"), {"command": "rm -rf /tmp/x"})
    assert "rm -rf /tmp/x" in desc          # literal command, not a summary


def test_rule_of_two_risk_requires_untrusted_and_destructive():
    p = Policy(PolicyConfig())
    shell = _spec("run_shell")
    # destructive + untrusted present → risk
    assert p.is_rule_of_two_risk(shell, {"command": "rm -rf /"}, untrusted_present=True)
    # destructive but no untrusted content → not a rule-of-two block
    assert not p.is_rule_of_two_risk(shell, {"command": "rm -rf /"}, untrusted_present=False)
    # untrusted present but reversible action → not destructive → no block
    reversible = _spec("type_text", permission="input", impact="reversible")
    assert not p.is_rule_of_two_risk(reversible, {"text": "hi"}, untrusted_present=True)


def test_spawn_depth_cap(monkeypatch, tmp_path):
    from aether.fleet.manager import SessionManager
    SessionManager.reset()
    m = SessionManager.get()
    m.configure({"worktrees": {"enabled": False}, "max_spawn_depth": 2},
                approved_roots=[str(tmp_path)])
    monkeypatch.setenv("AETHER_SPAWN_DEPTH", "2")   # already at the cap
    with pytest.raises(ValueError, match="fork-bomb"):
        m.spawn(agent_type="claude", prompt="x", workspace=str(tmp_path))
    SessionManager.reset()


def test_child_env_increments_spawn_depth(monkeypatch):
    from aether.tools.delegation import build_subprocess_env
    monkeypatch.setenv("AETHER_SPAWN_DEPTH", "0")
    assert build_subprocess_env()["AETHER_SPAWN_DEPTH"] == "1"
    monkeypatch.setenv("AETHER_SPAWN_DEPTH", "1")
    assert build_subprocess_env()["AETHER_SPAWN_DEPTH"] == "2"
