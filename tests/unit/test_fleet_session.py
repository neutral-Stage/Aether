"""Fleet session tests against the fake claude CLI fixture (no API keys)."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from aether.fleet.claude_session import ClaudeCodeSession
from aether.fleet.pty_session import PTYSession

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_claude.py"


def wait_for(cond, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def claude(tmp_path):
    os.chmod(FIXTURE, 0o755)
    s = ClaudeCodeSession(
        binary=str(FIXTURE),
        session_id="t1",
        label="test-claude",
        agent_type="claude",
        prompt="fix the tests",
        workspace=str(tmp_path),
    )
    yield s
    s.stop(grace_sec=0.5)


def test_claude_stream_lifecycle(claude):
    claude.start()
    assert wait_for(lambda: claude.state == "awaiting_input"), claude.status_dict()
    kinds = {e.kind for e in claude.tail(50)}
    assert {"text", "tool", "result"} <= kinds
    texts = [e.content for e in claude.tail(50) if e.kind == "text"]
    assert any("fix the tests" in t for t in texts)
    assert claude.claude_session_id == "fake-123"
    assert claude.cost_usd == pytest.approx(0.01)
    assert "turn 1 done" in claude.result_summary


def test_claude_multi_turn_steering(claude):
    claude.start()
    assert wait_for(lambda: claude.state == "awaiting_input")
    assert claude.send("now update the docs") is True
    assert wait_for(lambda: "turn 2 done" in claude.result_summary)
    # cumulative total_cost_usd → per-turn deltas summed
    assert claude.cost_usd == pytest.approx(0.02)
    assert claude.state == "awaiting_input"


def test_claude_stop_and_events_since(claude):
    claude.start()
    assert wait_for(lambda: claude.state == "awaiting_input")
    seq = claude.tail(1)[-1].seq
    claude.stop(grace_sec=0.5)
    assert claude.state == "stopped"
    assert claude.send("too late") is False
    fresh = claude.events_since(seq)
    assert all(e.seq > seq for e in fresh)


def test_claude_tolerant_parser(claude):
    # unknown/invalid NDJSON lines must degrade to text events, not crash
    claude._handle_line("not json at all")
    claude._handle_line('{"type": "mystery", "x": 1}')
    kinds = [e.kind for e in claude.tail(10)]
    assert kinds == ["text", "text"]


def test_pty_terminal_echo(tmp_path):
    s = PTYSession(
        session_id="t2",
        label="term",
        agent_type="terminal",
        prompt="echo fleet-pty-ok",
        workspace=str(tmp_path),
        idle_detect_sec=1.0,
    )
    s.start()
    try:
        assert wait_for(
            lambda: any("fleet-pty-ok" in e.content for e in s.tail(100)), timeout=15
        ), [e.content for e in s.tail(100)]
        # quiescence heuristic flips running → awaiting_input
        assert wait_for(lambda: s.state == "awaiting_input", timeout=10)
        assert s.send("exit") is True
        assert wait_for(lambda: s.state in ("done", "error"), timeout=10)
    finally:
        s.stop(grace_sec=0.5)
