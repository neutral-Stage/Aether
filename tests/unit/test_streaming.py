"""Token streaming: OpenAI-compatible and Anthropic clients, failover, token events."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import pytest

from aether.core import llm
from aether.core import stop as stop_ctl
from aether.core.llm import LLMResponse, TokenGate, step_with_tokens
from aether.core.llm_errors import FailoverLLMClient


def _chunk(content=None, calls=None, finish=None, usage=None):  # noqa: ANN001, ANN202
    choices = [] if content is None and calls is None and finish is None else [
        NS(delta=NS(content=content, tool_calls=calls), finish_reason=finish)]
    return NS(choices=choices, usage=usage)


def _call(index, id=None, name=None, args=None):  # noqa: A002, ANN001, ANN202
    return NS(index=index, id=id, function=NS(name=name, arguments=args))


class FakeCompletions:
    def __init__(self, chunks, reject_stream_options=False, explode_at=None):  # noqa: ANN001
        self.chunks = chunks
        self.reject = reject_stream_options
        self.explode_at = explode_at
        self.kwargs: list[dict] = []

    def create(self, **kw):  # noqa: ANN003, ANN201
        self.kwargs.append(kw)
        if self.reject and "stream_options" in kw:
            raise ValueError("unknown field stream_options")

        def gen():  # noqa: ANN202
            for i, c in enumerate(self.chunks):
                if self.explode_at == i:
                    raise ConnectionError("stream closed")
                yield c
        return gen()


def _fake_openai(monkeypatch, completions):  # noqa: ANN001, ANN202
    import openai

    class Fake:
        def __init__(self, **kw) -> None:  # noqa: ANN003
            self.chat = NS(completions=completions)

    monkeypatch.setattr(openai, "OpenAI", Fake)


STREAM = [_chunk("Opening "), _chunk("Safari."),
          _chunk(calls=[_call(0, id="c1", name="open_app", args='{"na')]),
          _chunk(calls=[_call(0, args='me": "Safari"}')]),
          _chunk(finish="tool_calls"),
          _chunk(usage=NS(prompt_tokens=12, completion_tokens=7))]


def test_openai_stream_assembles_text_calls_and_usage(monkeypatch) -> None:  # noqa: ANN001
    comp = FakeCompletions(STREAM)
    _fake_openai(monkeypatch, comp)
    client = llm.OpenAICompatibleClient(api_key="k", model="glm")
    got: list[str] = []
    resp = client.step("sys", [{"role": "user", "content": "hi"}],
                       [{"name": "open_app", "input_schema": {"type": "object"}}],
                       on_token=got.append)
    assert got == ["Opening ", "Safari."]
    assert resp.tool_calls == [{"id": "c1", "name": "open_app", "input": {"name": "Safari"}}]
    assert resp.stop_reason == "tool_use" and resp.text == ""
    assert (resp.input_tokens, resp.output_tokens) == (12, 7)
    assert comp.kwargs[0]["stream"] is True and comp.kwargs[0]["stream_options"]


def test_stream_options_fallback_and_plain_path(monkeypatch) -> None:  # noqa: ANN001
    comp = FakeCompletions([_chunk("Hi"), _chunk(finish="stop")], reject_stream_options=True)
    _fake_openai(monkeypatch, comp)
    client = llm.OpenAICompatibleClient(api_key="k", model="glm")
    resp = client.step("s", [{"role": "user", "content": "x"}], [], on_token=lambda t: None)
    assert resp.text == "Hi" and len(comp.kwargs) == 2 and "stream_options" not in comp.kwargs[1]


def test_stop_mid_stream_raises_stop(monkeypatch) -> None:  # noqa: ANN001
    comp = FakeCompletions([_chunk("a"), _chunk("b")], explode_at=1)
    _fake_openai(monkeypatch, comp)
    client = llm.OpenAICompatibleClient(api_key="k", model="glm")

    def on_token(text: str) -> None:
        stop_ctl.trigger("test")

    with pytest.raises(stop_ctl.StopRequested):
        client.step("s", [{"role": "user", "content": "x"}], [], on_token=on_token)
    stop_ctl.reset()
    comp2 = FakeCompletions([_chunk("a"), _chunk("b")], explode_at=1)
    _fake_openai(monkeypatch, comp2)
    with pytest.raises(ConnectionError):
        client.step("s", [{"role": "user", "content": "x"}], [], on_token=lambda t: None)


def test_token_gate() -> None:
    out: list[str] = []
    g = TokenGate(out.append)
    for c in ("I'll", " open", " it"):
        g(c)
    assert "".join(out) == "I'll open it"
    hidden: list[str] = []
    g = TokenGate(hidden.append)
    for c in ('  {"na', 'me": "open_app"}'):
        g(c)
    g.flush()
    assert hidden == []
    short: list[str] = []
    g = TokenGate(short.append)
    g("Ok")
    g.flush()
    assert short == ["Ok"]
    fence: list[str] = []
    g = TokenGate(fence.append)
    g("```json\n{}")
    assert fence == []


class Plain:
    def step(self, system, messages, tools, *, abort_event=None):  # noqa: ANN001, ANN201
        return LLMResponse(text="whole reply", tool_calls=[], raw_content=[],
                           stop_reason="end_turn", backend="plain")


class Streamy:
    def __init__(self, fail_after: int | None = None, transient: bool = True) -> None:
        self.fail_after = fail_after
        self.transient = transient

    def step(self, system, messages, tools, *, abort_event=None, on_token=None):  # noqa: ANN001, ANN201
        for i, part in enumerate(("a", "b")):
            if self.fail_after is not None and i == self.fail_after:
                raise RuntimeError("503 service unavailable" if self.transient else "bad")
            if on_token:
                on_token(part)
        return LLMResponse(text="ab", tool_calls=[], raw_content=[], stop_reason="end_turn",
                           backend="streamy")


def test_step_with_tokens_and_failover() -> None:
    got: list[str] = []
    assert step_with_tokens(Plain(), "s", [], [], on_token=got.append).text == "whole reply"
    assert got == ["whole reply"]
    got.clear()
    step_with_tokens(Streamy(), "s", [], [], on_token=got.append)
    assert got == ["a", "b"]
    got.clear()
    fo = FailoverLLMClient([("first", Streamy(fail_after=0)), ("second", Streamy())])
    assert fo.step("s", [], [], on_token=got.append).backend == "streamy" and got == ["a", "b"]
    got.clear()
    late = FailoverLLMClient([("first", Streamy(fail_after=1)), ("second", Streamy())])
    with pytest.raises(RuntimeError):
        late.step("s", [], [], on_token=got.append)       # "a" already shown: no retry
    assert got == ["a"]
    got.clear()
    mixed = FailoverLLMClient([("p", Plain())])
    mixed.step("s", [], [], on_token=got.append)
    assert got == ["whole reply"]


def test_anthropic_stream(monkeypatch) -> None:  # noqa: ANN001
    import anthropic

    final = NS(content=[NS(type="text", text="Hello there"),
                        NS(type="tool_use", id="t1", name="finish", input={"message": "ok"})],
               stop_reason="tool_use", usage=NS(input_tokens=3, output_tokens=4))

    class Stream:
        text_stream = iter(["Hello", " there"])

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *a) -> None:  # noqa: ANN002
            return None

        def get_final_message(self):  # noqa: ANN201
            return final

    seen = {}

    class Fake:
        def __init__(self, **kw) -> None:  # noqa: ANN003
            self.messages = NS(stream=lambda **k: seen.update(k) or Stream(),
                               create=lambda **k: final)

    monkeypatch.setattr(anthropic, "Anthropic", Fake)
    got: list[str] = []
    resp = llm.LLM(api_key="k", model="claude-sonnet-5").step(
        "sys", [{"role": "user", "content": "hi"}], [], on_token=got.append)
    assert got == ["Hello", " there"] and resp.text == "Hello there"
    assert resp.tool_calls[0]["name"] == "finish" and seen["system"] == "sys"
    assert "temperature" not in str(seen)


def test_agent_emits_token_events(minimal_config, monkeypatch) -> None:  # noqa: ANN001
    from aether.core.orchestrator import Agent
    from aether.core.router import RouteDecision, RouteTier

    agent = Agent(minimal_config, hud=None)
    events: list[dict] = []
    agent.emit = events.append
    monkeypatch.setattr(agent.world, "refresh", lambda force=False: {})
    monkeypatch.setattr(agent, "say", lambda text: None)
    monkeypatch.setattr(agent.router, "route",
                        lambda *a, **k: RouteDecision(tier=RouteTier.CLOUD_FRONTIER, reason="t"))

    class Talker:
        def step(self, system, messages, tools, *, abort_event=None, on_token=None):  # noqa: ANN001, ANN201
            for part in ("All ", "done."):
                on_token(part)
            return LLMResponse(text="All done.", tool_calls=[], raw_content=[
                {"type": "text", "text": "All done."}], stop_reason="end_turn", backend="t")

    monkeypatch.setattr(agent.router, "pick_client", lambda d: Talker())
    asyncio.run(agent.run_async("say hi", run_id="tok"))
    tokens = [e for e in events if e["type"] == "token"]
    assert "".join(e["text"] for e in tokens) == "All done."
    assert any(e["type"] == "token_end" for e in events)
