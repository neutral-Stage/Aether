"""Self-correction must never leave tool_use blocks without tool_results.

Regression for the orchestrator's old self-correction path: after a failed
verification it made an extra model call and appended that reply's tool calls
to the conversation without executing them. The next request then carried
tool_use blocks with no matching tool_result — rejected by every provider.
"""
from __future__ import annotations

import asyncio
import pytest

from aether.core.config import Config
from aether.core.llm import LLMResponse
from aether.core.orchestrator import Agent
from aether.core.router import RouteDecision, RouteTier


def _assert_valid_conversation(messages: list[dict]) -> None:
    """Every assistant turn with tool_use is answered by the next user turn."""
    for i, msg in enumerate(messages):
        if msg["role"] != "assistant":
            continue
        content = msg["content"] if isinstance(msg["content"], list) else []
        ids = [b["id"] for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        if not ids:
            continue
        assert i + 1 < len(messages), "tool_use turn is the last message"
        nxt = messages[i + 1]
        assert nxt["role"] == "user"
        answered = [b["tool_use_id"] for b in nxt["content"] if b.get("type") == "tool_result"]
        assert answered == ids, f"tool_use {ids} answered by {answered}"


class ScriptedClient:
    """Returns scripted tool calls and validates each request it receives."""

    def __init__(self, script: list[list[tuple[str, dict]]]) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def step(self, system, messages, tools, *, abort_event=None):  # noqa: ANN001
        _assert_valid_conversation(messages)
        self.calls.append({"system": system, "n_messages": len(messages)})
        planned = self.script.pop(0) if self.script else [("finish", {"message": "done"})]
        blocks = []
        for i, (name, args) in enumerate(planned):
            blocks.append({"type": "tool_use", "id": f"tu{len(self.calls)}_{i}",
                           "name": name, "input": args})
        return LLMResponse(
            text="",
            tool_calls=[{"id": b["id"], "name": b["name"], "input": b["input"]} for b in blocks],
            raw_content=blocks,
            stop_reason="tool_use",
            backend="fake",
        )


@pytest.fixture
def agent(minimal_config: Config, monkeypatch: pytest.MonkeyPatch) -> Agent:
    minimal_config.raw["agent"]["max_steps"] = 6
    a = Agent(minimal_config, hud=None)
    monkeypatch.setattr(a.world, "refresh", lambda force=False: {})
    monkeypatch.setattr(a, "say", lambda text: None)
    monkeypatch.setattr(
        a.router, "route",
        lambda *args, **kw: RouteDecision(tier=RouteTier.CLOUD_FRONTIER, reason="test"),
    )
    return a


def _run(agent: Agent, client: ScriptedClient, monkeypatch: pytest.MonkeyPatch,
         verify_results: list[bool]) -> str:
    monkeypatch.setattr(agent.router, "pick_client", lambda decision: client)
    outcomes = list(verify_results)

    def fake_verify(expected, observation):  # noqa: ANN001
        ok = outcomes.pop(0) if outcomes else True
        agent.world.needs_replan = not ok
        return ok

    monkeypatch.setattr(agent.world, "verify", fake_verify)
    monkeypatch.setattr(agent.registry, "dispatch",
                        lambda name, args, ctx: f"did {name}")
    return asyncio.run(agent.run_async("click the OK button", run_id="t"))


@pytest.mark.unit
def test_failed_verification_keeps_conversation_valid(agent, monkeypatch) -> None:
    client = ScriptedClient([
        [("click", {"element_index": 1})],   # verification fails
        [("click", {"element_index": 2})],   # corrected attempt succeeds
        [("finish", {"message": "Clicked OK."})],
    ])
    final = _run(agent, client, monkeypatch, verify_results=[False, True])
    assert final == "Clicked OK."
    assert len(client.calls) == 3


@pytest.mark.unit
def test_correction_note_reaches_the_next_step(agent, monkeypatch) -> None:
    client = ScriptedClient([
        [("click", {"element_index": 1})],
        [("finish", {"message": "ok"})],
    ])
    _run(agent, client, monkeypatch, verify_results=[False])
    assert "SELF-CORRECTION" not in client.calls[0]["system"]
    assert "SELF-CORRECTION" in client.calls[1]["system"]
    assert "VERIFY FAILED after click" in client.calls[1]["system"]


@pytest.mark.unit
def test_correction_is_consumed_once(agent, monkeypatch) -> None:
    client = ScriptedClient([
        [("click", {"element_index": 1})],
        [("click", {"element_index": 2})],
        [("finish", {"message": "ok"})],
    ])
    _run(agent, client, monkeypatch, verify_results=[False, True])
    assert "SELF-CORRECTION" in client.calls[1]["system"]
    assert "SELF-CORRECTION" not in client.calls[2]["system"]


@pytest.mark.unit
def test_validator_rejects_orphaned_tool_use() -> None:
    bad = [
        {"role": "user", "content": "goal"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "x", "input": {}}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "b", "name": "y", "input": {}}]},
    ]
    with pytest.raises(AssertionError):
        _assert_valid_conversation(bad)
