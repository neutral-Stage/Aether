"""Conversations, ask_user, batch_actions and structured run events."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import pytest

from aether.core import orchestrator as orch
from aether.core import stop as stop_ctl
from aether.core.config import Config
from aether.core.llm import LLMResponse
from aether.core.orchestrator import Agent
from aether.core.router import RouteDecision, RouteTier
from sidecar import questions, session_store

# ---- session store ------------------------------------------------------------------


def test_session_history_roundtrip() -> None:
    sid = session_store.create_session("Rename the report   to final")
    session_store.add_turn(sid, "r1", "rename report.pdf to final.pdf", "Renamed it.",
                           ["open Finder", "click 'report.pdf'"])
    session_store.add_turn(sid, "r2", "now zip it", "Stopped by user.", [], status="stopped")
    hist = session_store.history(sid)
    assert [m["role"] for m in hist] == ["user", "assistant", "user", "assistant"]
    assert hist[1]["content"].startswith("Renamed it.") and "click 'report.pdf'" in hist[1]["content"]
    assert "ended as: stopped" in hist[3]["content"]
    listed = session_store.list_sessions()
    assert listed[0]["id"] == sid and listed[0]["turns"] == 2
    assert listed[0]["title"] == "Rename the report to final"
    full = session_store.get_session(sid)
    assert [t["run_id"] for t in full["turns"]] == ["r1", "r2"]
    assert session_store.delete_session(sid) and session_store.get_session(sid) is None
    assert session_store.history(sid) == []


def test_history_keeps_only_recent_turns() -> None:
    sid = session_store.create_session("x")
    for i in range(15):
        session_store.add_turn(sid, f"r{i}", f"goal {i}", f"done {i}")
    hist = session_store.history(sid, max_turns=3)
    assert [m["content"] for m in hist if m["role"] == "user"] == ["goal 12", "goal 13", "goal 14"]


@pytest.mark.parametrize(("history", "roles"), [
    ([{"role": "assistant", "content": "hi"}, {"role": "user", "content": "a"},
      {"role": "assistant", "content": "b"}], ["user", "assistant"]),
    ([{"role": "user", "content": "a"}, {"role": "user", "content": "b"},
      {"role": "assistant", "content": "c"}], ["user", "assistant"]),
    ([{"role": "user", "content": "a"}], []),
    ([{"role": "system", "content": "x"}, {"role": "user", "content": ""}], []),
    (None, []),
])
def test_clean_history(history, roles) -> None:  # noqa: ANN001
    assert [m["role"] for m in orch.clean_history(history)] == roles


# ---- questions bridge -----------------------------------------------------------------

def test_question_answered_skipped_and_unanswered() -> None:
    async def scenario() -> tuple:
        sent: list[dict] = []

        async def broadcast(ev: dict) -> None:
            sent.append(ev)

        task = asyncio.ensure_future(questions.request_answer(
            "Which folder?", ["Downloads", "Desktop"], run_id="r", broadcaster=broadcast))
        await asyncio.sleep(0)
        assert questions.resolve_answer(sent[0]["request_id"], "  Desktop ")
        answered = await task
        task = asyncio.ensure_future(questions.request_answer("Again?", broadcaster=broadcast))
        await asyncio.sleep(0)
        questions.resolve_answer(sent[1]["request_id"], "")
        skipped = await task
        timed_out = await questions.request_answer("Late?", broadcaster=broadcast,
                                                   timeout_sec=0.01)
        return sent, answered, skipped, timed_out

    sent, answered, skipped, timed_out = asyncio.run(scenario())
    assert sent[0]["type"] == "question" and sent[0]["options"] == ["Downloads", "Desktop"]
    assert (answered, skipped, timed_out) == ("Desktop", None, None)
    assert questions.pending_count() == 0
    assert not questions.resolve_answer("nope", "x")
    assert asyncio.run(questions.request_answer("no app?")) is None


# ---- agent loop ------------------------------------------------------------------------

class Scripted:
    def __init__(self, calls: list) -> None:
        self.calls = list(calls)
        self.requests: list[list[dict]] = []

    def step(self, system, messages, tools, *, abort_event=None):  # noqa: ANN001
        import copy

        self.requests.append(copy.deepcopy(messages))
        name, args = self.calls.pop(0) if self.calls else ("finish", {"message": "done"})
        call = {"id": f"t{len(self.requests)}", "name": name, "input": args}
        return LLMResponse(text="", tool_calls=[call], raw_content=[{"type": "tool_use", **call}],
                           stop_reason="tool_use", backend="fake")


@pytest.fixture
def agent(minimal_config: Config, monkeypatch: pytest.MonkeyPatch) -> Agent:
    minimal_config.raw["agent"]["max_steps"] = 6
    a = Agent(minimal_config, hud=None)
    monkeypatch.setattr(a.world, "refresh", lambda force=False: {})
    monkeypatch.setattr(a, "say", lambda text: None)
    monkeypatch.setattr(a.router, "route",
                        lambda *x, **k: RouteDecision(tier=RouteTier.CLOUD_FRONTIER, reason="t"))
    a.events = []
    a.emit = a.events.append
    return a


def _run(agent: Agent, client: Scripted, monkeypatch, goal: str = "do it",  # noqa: ANN001
         history: list | None = None) -> tuple[str, list]:
    dispatched: list = []
    monkeypatch.setattr(agent.router, "pick_client", lambda d: client)
    monkeypatch.setattr(agent.registry, "dispatch",
                        lambda n, a, c: dispatched.append((n, a)) or f"did {n}")
    final = asyncio.run(agent.run_async(goal, run_id="s", history=history))
    return final, dispatched


def _result(client: Scripted, i: int) -> str:
    return client.requests[i][-1]["content"][0]["content"]


def test_history_precedes_the_goal(agent, monkeypatch) -> None:  # noqa: ANN001
    client = Scripted([])
    hist = [{"role": "user", "content": "open report.pdf"},
            {"role": "assistant", "content": "Opened it."}]
    _run(agent, client, monkeypatch, goal="now print it", history=hist)
    assert client.requests[0][:3] == [*hist, {"role": "user", "content": "now print it"}]


def test_ask_user_round_trip(agent, monkeypatch) -> None:  # noqa: ANN001
    asked: list = []

    async def ask(q: str, opts: list[str]) -> str:
        asked.append((q, opts))
        return "the blue one"

    agent.ask_async = ask
    client = Scripted([("ask_user", {"question": "Which file?", "options": ["a", "b"]})])
    _run(agent, client, monkeypatch)
    assert asked == [("Which file?", ["a", "b"])]
    assert _result(client, 1) == "The user answered: the blue one"
    assert {"type": "question", "question": "Which file?", "options": ["a", "b"]} in agent.events


@pytest.mark.parametrize("question", [
    "What is your Mac password?", "Please read me the verification code",
    "What's the card number?", "Enter your 2FA code"])
def test_ask_user_never_asks_for_secrets(agent, monkeypatch, question) -> None:  # noqa: ANN001
    async def ask(q, o):  # noqa: ANN001, ANN202
        raise AssertionError("must not be asked")

    agent.ask_async = ask
    client = Scripted([("ask_user", {"question": question})])
    _run(agent, client, monkeypatch)
    assert "never asks for passwords" in _result(client, 1)


def test_ask_user_unanswered_and_stop(agent, monkeypatch) -> None:  # noqa: ANN001
    async def no_answer(q, o):  # noqa: ANN001, ANN202
        return None

    agent.ask_async = no_answer
    client = Scripted([("ask_user", {"question": "Which?"})])
    _run(agent, client, monkeypatch)
    assert "did not answer" in _result(client, 1)

    async def forever(q, o):  # noqa: ANN001, ANN202
        stop_ctl.trigger("test")
        await asyncio.sleep(30)

    agent.ask_async = forever
    client = Scripted([("ask_user", {"question": "Which?"})])
    final, _ = _run(agent, client, monkeypatch)
    assert final == "Stopped by user."


def test_batch_runs_each_action_through_the_gate(agent, monkeypatch) -> None:  # noqa: ANN001
    batch = {"actions": [
        {"tool": "click_element", "args": {"name": "Search"}},
        {"tool": "type_text", "args": {"text": "weather"}},
        {"tool": "press_key", "args": {"key": "return"}}]}
    client = Scripted([("batch_actions", batch)])
    _, dispatched = _run(agent, client, monkeypatch)
    assert [n for n, _ in dispatched] == ["click_element", "type_text", "press_key"]
    out = _result(client, 1)
    assert out.splitlines()[0].startswith("1. click 'Search'") and "3. press return" in out
    kinds = [e["type"] for e in agent.events if e["type"] in ("tool_call", "tool_result")]
    assert kinds == ["tool_call", "tool_result"] * 3


def test_batch_stops_at_decline_and_rejects_bad_actions(agent, monkeypatch) -> None:  # noqa: ANN001
    confirms: list[str] = []

    async def deny(text: str) -> bool:
        confirms.append(text)
        return False

    agent.confirm_async = deny
    batch = {"actions": [
        {"tool": "click_element", "args": {"name": "Select All"}},
        {"tool": "click_element", "args": {"name": "Empty Trash"}},
        {"tool": "press_key", "args": {"key": "return"}}]}
    client = Scripted([("batch_actions", batch),
                       ("batch_actions", {"actions": [{"tool": "run_shell",
                                                       "args": {"command": "ls"}}]}),
                       ("batch_actions", {"actions": [{"tool": "wait", "args": {}}] * 6}),
                       ("batch_actions", {"actions": [{"tool": "batch_actions",
                                                       "args": {"actions": []}}]}),
                       ("batch_actions", {"actions": [{"tool": "wait", "args": ["bad"]}]})])
    _, dispatched = _run(agent, client, monkeypatch)
    assert [n for n, _ in dispatched] == ["click_element", "wait"]   # "Select All", last wait
    assert confirms and "Empty Trash" in confirms[0]
    assert "Stopped after action 2" in _result(client, 1)
    assert "not allowed in a batch" in _result(client, 2)
    assert "at most 5" in _result(client, 3)
    assert "not allowed in a batch" in _result(client, 4)
    assert _result(client, 5).startswith("1. wait")      # non-dict args → {}


def test_events_for_text_and_screenshots(agent, monkeypatch) -> None:  # noqa: ANN001
    class Talky(Scripted):
        def step(self, *a, **k):  # noqa: ANN002, ANN003, ANN202
            r = super().step(*a, **k)
            r.text = "Looking at the screen."
            return r

    def dispatch(n, a, c):  # noqa: ANN001, ANN202
        if n == "screenshot":
            c.pending_images.append("/tmp/x.png")
        return "ok"

    monkeypatch.setattr(agent.router, "pick_client", lambda d: client)
    monkeypatch.setattr(agent.registry, "dispatch", dispatch)
    client = Talky([("screenshot", {})])
    asyncio.run(agent.run_async("look", run_id="e"))
    types = [e["type"] for e in agent.events]
    assert "text" in types and "screenshot" in types
    shot = next(e for e in agent.events if e["type"] == "screenshot")
    assert shot["path"] == "/tmp/x.png" and shot["tool"] == "screenshot"


# ---- sidecar wiring ---------------------------------------------------------------------

class FakeAgent:
    seen: list = []

    def __init__(self, cfg, hud=None) -> None:  # noqa: ANN001
        self.cfg = cfg
        self.world = NS(set_screen_stream=lambda s: None, snapshot=lambda: {},
                        task_trace=lambda: ["open Safari"])

    async def run_async(self, goal, *, run_id=None, reset_stop=True, history=None):  # noqa: ANN001
        FakeAgent.seen.append((goal, history))
        return f"finished {goal}"


def test_run_endpoint_threads_sessions(sidecar_client, monkeypatch) -> None:  # noqa: ANN001
    from sidecar import server

    FakeAgent.seen = []
    monkeypatch.setattr(server, "Agent", FakeAgent)
    first = sidecar_client.post("/run", json={"goal": "open safari", "stream": False,
                                              "local_only": True}).json()
    sid = first["session_id"]
    assert first["result"] == "finished open safari" and sid
    second = sidecar_client.post("/run", json={"goal": "now go back", "stream": False,
                                               "local_only": True, "session_id": sid}).json()
    assert second["session_id"] == sid
    assert FakeAgent.seen[0][1] == []
    assert FakeAgent.seen[1][1][0] == {"role": "user", "content": "open safari"}
    assert "open Safari" in FakeAgent.seen[1][1][1]["content"]
    listed = sidecar_client.get("/sessions").json()["sessions"]
    assert listed[0]["id"] == sid and listed[0]["turns"] == 2
    assert sidecar_client.post("/run", json={"goal": "x", "stream": False, "local_only": True,
                                             "session_id": "missing"}).status_code == 404
    assert sidecar_client.delete(f"/sessions/{sid}").status_code == 200
    assert sidecar_client.get(f"/sessions/{sid}").status_code == 404
    assert sidecar_client.post("/answer", json={"request_id": "nope"}).status_code == 404
