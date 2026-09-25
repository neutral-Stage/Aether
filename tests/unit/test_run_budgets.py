"""Long runs: step and cost budgets, context collapse, the say-do guard,
malformed tool calls, and the single prompt source."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from aether.core import llm
from aether.core import orchestrator as orch
from aether.core.config import Config
from aether.core.llm import LLMResponse
from aether.core.orchestrator import Agent
from aether.core.router import RouteDecision, RouteTier


# ---- context collapse ---------------------------------------------------------------

def _turns(n: int, size: int) -> list[dict]:
    msgs: list[dict] = [{"role": "user", "content": "goal"}]
    for i in range(n):
        msgs.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "get_screen_context", "input": {}}]})
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}",
             "content": f"Frontmost app: App{i}\n" + "x" * size}]})
    return msgs


def test_collapse_keeps_recent_and_first_lines() -> None:
    msgs = _turns(10, 5000)
    n = llm.collapse_tool_results(msgs, budget_chars=20000, keep_recent=3)
    texts = [llm.tool_result_text(b) for _, b in llm._tool_results(msgs)]  # noqa: SLF001
    assert n >= 1
    assert all("collapsed" not in t for t in texts[-3:])
    assert texts[0].startswith("Frontmost app: App0") and "collapsed" in texts[0]
    assert sum(len(t) for t in texts) <= 20000 + 3 * 200
    assert llm.collapse_tool_results(msgs, budget_chars=20000, keep_recent=3) == 0


def test_collapse_noop_under_budget_or_disabled() -> None:
    msgs = _turns(3, 100)
    assert llm.collapse_tool_results(msgs, budget_chars=60000) == 0
    assert llm.collapse_tool_results(_turns(10, 5000), budget_chars=0) == 0


def test_collapse_keeps_images_in_list_content() -> None:
    msgs = _turns(8, 5000)
    first = llm._tool_results(msgs)[0][1]  # noqa: SLF001
    first["content"] = [{"type": "text", "text": "shot\n" + "y" * 5000},
                        {"type": "image", "source": {"data": "x"}}]
    llm.collapse_tool_results(msgs, budget_chars=10000, keep_recent=2)
    kinds = [c["type"] for c in first["content"]]
    assert kinds == ["text", "image"] and "collapsed" in first["content"][0]["text"]


# ---- argument repair ----------------------------------------------------------------

@pytest.mark.parametrize(("raw", "expect"), [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Here you go: {"a": 1} hope it helps', {"a": 1}),
    ('{"a": 1,}', {"a": 1}),
    (json.dumps(json.dumps({"a": 1})), {"a": 1}),
    ("", {}), (None, {}),
    ("{not json", None), ("[1, 2]", None),
])
def test_repair_json_args(raw, expect) -> None:  # noqa: ANN001
    assert llm.repair_json_args(raw) == expect


def _fake_openai(monkeypatch, message) -> None:  # noqa: ANN001
    class Completions:
        def create(self, **kw):  # noqa: ANN003, ANN202
            return NS(choices=[NS(message=message, finish_reason="stop")],
                      usage=NS(prompt_tokens=1, completion_tokens=1))

    class FakeOpenAI:
        def __init__(self, **kw):  # noqa: ANN003
            self.chat = NS(completions=Completions())

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)


TOOLS = [{"name": "open_app", "description": "", "input_schema": {"type": "object"}}]


def test_openai_client_marks_unparseable_arguments(monkeypatch) -> None:
    bad = NS(id="c1", function=NS(name="open_app", arguments="{name: Safari"))
    _fake_openai(monkeypatch, NS(content="", tool_calls=[bad]))
    client = llm.OpenAICompatibleClient(api_key="k", model="m")
    resp = client.step("s", [{"role": "user", "content": "hi"}], TOOLS)
    assert resp.tool_calls[0]["input"] == {llm.INVALID_ARGS_KEY: "{name: Safari"}


def test_openai_client_accepts_tool_call_written_as_text(monkeypatch) -> None:
    text = 'Opening it.\n```json\n{"name": "open_app", "arguments": {"name": "Safari"}}\n```'
    _fake_openai(monkeypatch, NS(content=text, tool_calls=None))
    client = llm.OpenAICompatibleClient(api_key="k", model="m")
    resp = client.step("s", [{"role": "user", "content": "hi"}], TOOLS)
    assert [(c["name"], c["input"]) for c in resp.tool_calls] == [("open_app", {"name": "Safari"})]
    assert resp.stop_reason == "tool_use"
    unknown = text.replace("open_app", "format_disk")
    _fake_openai(monkeypatch, NS(content=unknown, tool_calls=None))
    assert client.step("s", [{"role": "user", "content": "hi"}], TOOLS).tool_calls == []


# ---- say-do guard -------------------------------------------------------------------

@pytest.mark.parametrize(("text", "promise"), [
    ("I'll open Safari and search for it.", True),
    ("Let me click the Save button.", True),
    ("Now I will type the address.", True),
    ("I'm going to quickly check the Downloads folder.", True),
    ("Done! I opened Safari and saved the page.", False),
    ("Let me know if you need anything else, I can open more tabs.", False),
    ("I'll be here if you need me.", False),
    ("", False), (None, False),
])
def test_promises_action(text, promise) -> None:  # noqa: ANN001
    assert orch.promises_action(text) is promise


# ---- agent loop -----------------------------------------------------------------------

class Scripted:
    def __init__(self, replies: list) -> None:
        self.replies = list(replies)
        self.systems: list[str] = []
        self.requests: list[list[dict]] = []

    def step(self, system, messages, tools, *, abort_event=None):  # noqa: ANN001
        import copy

        self.systems.append(system)
        self.requests.append(copy.deepcopy(messages))
        r = self.replies.pop(0) if self.replies else ("finish", {"message": "done"})
        if isinstance(r, str):
            return LLMResponse(text=r, tool_calls=[], raw_content=[{"type": "text", "text": r}],
                               stop_reason="end_turn", backend="fake")
        name, args = r
        call = {"id": f"t{len(self.systems)}", "name": name, "input": args}
        return LLMResponse(text="", tool_calls=[call], raw_content=[{"type": "tool_use", **call}],
                           stop_reason="tool_use", backend="fake")


@pytest.fixture
def agent(minimal_config: Config, monkeypatch: pytest.MonkeyPatch) -> Agent:
    minimal_config.raw["agent"]["max_steps"] = 8
    a = Agent(minimal_config, hud=None)
    monkeypatch.setattr(a.world, "refresh", lambda force=False: {})
    monkeypatch.setattr(a, "say", lambda text: None)
    monkeypatch.setattr(a.router, "route",
                        lambda *x, **k: RouteDecision(tier=RouteTier.CLOUD_FRONTIER, reason="t"))
    return a


def _run(agent: Agent, client: Scripted, monkeypatch, dispatched: list | None = None) -> str:  # noqa: ANN001
    monkeypatch.setattr(agent.router, "pick_client", lambda d: client)
    monkeypatch.setattr(agent.registry, "dispatch",
                        lambda n, a, c: (dispatched.append(n) if dispatched is not None else None)
                        or "ok")
    return asyncio.run(agent.run_async("do the thing", run_id="b"))


def test_cost_cap_stops_the_run(agent, monkeypatch) -> None:  # noqa: ANN001
    agent.cfg.raw["agent"]["cost_cap_usd"] = 1.0
    spent = iter([0.0, 0.8, 1.2])
    monkeypatch.setattr(agent.metrics, "run_cost", lambda run_id=None: next(spent, 1.2))
    client = Scripted([("wait", {"seconds": 1})] * 5)
    final = _run(agent, client, monkeypatch)
    assert "cost limit" in final and "$1.20 of $1.00" in final
    assert len(client.systems) == 2
    assert "BUDGET" in client.systems[1] and "$0.80 of the $1.00" in client.systems[1]


def test_step_budget_note_at_three_quarters(agent, monkeypatch) -> None:  # noqa: ANN001
    client = Scripted([("wait", {"seconds": 1})] * 8)
    final = _run(agent, client, monkeypatch)
    assert "step limit (8 steps)" in final
    notes = ["BUDGET" in s for s in client.systems]
    assert notes == [False] * 5 + [True] * 3
    assert "step 6 of at most 8" in client.systems[5]


def test_say_do_guard_nudges_once(agent, monkeypatch) -> None:  # noqa: ANN001
    dispatched: list[str] = []
    client = Scripted(["I'll open Safari now.", ("open_app", {"name": "Safari"}),
                       "I'll open it again.", ])
    final = _run(agent, client, monkeypatch, dispatched)
    assert dispatched == ["open_app"]
    second = client.requests[1]
    assert second[-2] == {"role": "assistant", "content": "I'll open Safari now."}
    assert second[-1]["content"] == orch.SAY_DO_NUDGE
    assert final == "I'll open it again."       # only one nudge per run


def test_invalid_and_missing_arguments_never_dispatch(agent, monkeypatch) -> None:  # noqa: ANN001
    dispatched: list[str] = []
    client = Scripted([("run_shell", {llm.INVALID_ARGS_KEY: "{cmd: ls"}),
                       ("run_shell", {}),
                       ("finish", {"message": "ok"})])
    _run(agent, client, monkeypatch, dispatched)
    assert dispatched == []
    r1 = client.requests[1][-1]["content"][0]["content"]
    r2 = client.requests[2][-1]["content"][0]["content"]
    assert "not valid JSON" in r1 and "needs command" in r2


def test_system_prompt_has_one_source() -> None:
    from aether.core.config import ROOT

    text = (ROOT / "shared" / "prompts" / "system.txt").read_text().strip()
    assert orch.BASE_SYSTEM_PROMPT == text
    for rule in ("click_element", "menu_item", "`wait`", "scroll", "finish"):
        assert rule in text
