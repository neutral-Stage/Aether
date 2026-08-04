"""Sub-agent constraint propagation (Phase 17c).

Aether's policy — careful mode, capabilities, network_allowlist,
approved_file_roots — governs only this process. A delegated CLI inherits none
of it, and `delegate_to_coder` spawned `claude -p <PROMPT>` with no flags at
all, ignoring the very permission mode the fleet path already honoured. Phase
16 forces a confirm at the spawn hop under untrusted content; without these
flags the child was ungoverned once that hop was approved.
"""
from __future__ import annotations

import pytest

from aether.tools.delegation import AGENT_COMMANDS, _constraint_flags


@pytest.mark.parametrize("agent,mode,expected", [
    ("claude", "plan", ["--permission-mode", "plan"]),
    ("claude", "acceptEdits", ["--permission-mode", "acceptEdits"]),
    ("codex", "plan", ["--sandbox", "read-only"]),
    ("codex", "acceptEdits", ["--sandbox", "workspace-write"]),
])
def test_constraint_flags_applied(agent, mode, expected):
    assert _constraint_flags(agent, mode) == expected


@pytest.mark.parametrize("agent", ["opencode", "cursor"])
def test_clis_without_a_gate_get_no_flags(agent):
    """These expose no equivalent flag. Documented, not papered over: for them
    the only enforcement is process-level (validated cwd, scrubbed env,
    timeout, killpg)."""
    assert _constraint_flags(agent, "plan") == []


def test_bypass_is_an_explicit_opt_out():
    assert _constraint_flags("claude", "bypassPermissions") == []


def test_no_mode_adds_nothing():
    assert _constraint_flags("claude", "") == []


def test_unknown_mode_adds_nothing():
    assert _constraint_flags("claude", "nonsense") == []


def test_auto_is_not_a_binary_name():
    """agent='auto' is the DEFAULT. Keying the lookup on it rather than on the
    resolved binary would silently apply no flags in the common case."""
    assert _constraint_flags("auto", "plan") == []


@pytest.mark.parametrize("agent,mode,expected_argv", [
    ("claude", "plan", ["claude", "-p", "--permission-mode", "plan", "P"]),
    ("codex", "plan", ["codex", "exec", "--sandbox", "read-only", "P"]),
    ("opencode", "plan", ["opencode", "run", "P"]),
])
def test_resulting_argv(agent, mode, expected_argv):
    """The flags must land BEFORE the prompt, or the CLI reads them as prose."""
    cmd = AGENT_COMMANDS[agent]
    assert cmd + _constraint_flags(cmd[0], mode) + ["P"] == expected_argv
