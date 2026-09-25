"""Anthropic backend against the real SDK (1.x, httpx2) with a mocked transport.

Guards three real-install failures: the SDK rejecting an ``httpx`` client,
``analyze_image`` using a client attribute that was never set, and sampling
parameters that current Claude models reject with a 400.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

anthropic = pytest.importorskip("anthropic")
httpx2 = pytest.importorskip("httpx2")

from aether.core import llm as llm_mod  # noqa: E402
from aether.core.llm import LLM, anthropic_accepts_sampling  # noqa: E402


def _message(content: list[dict], stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_test", "type": "message", "role": "assistant",
        "model": "claude-sonnet-5", "content": content,
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Route the SDK's HTTP client through a MockTransport; capture request bodies."""
    state = SimpleNamespace(requests=[], replies=[])

    def handler(request: httpx2.Request) -> httpx2.Response:
        state.requests.append(json.loads(request.content))
        body = (state.replies.pop(0) if state.replies
                else _message([{"type": "text", "text": "ok"}]))
        return httpx2.Response(200, json=body)

    def factory(**kwargs):  # noqa: ANN003
        return httpx2.Client(transport=httpx2.MockTransport(handler))

    monkeypatch.setattr(anthropic, "DefaultHttpxClient", factory)
    return state


def test_sampling_support_by_model() -> None:
    assert not anthropic_accepts_sampling("claude-sonnet-5")
    assert not anthropic_accepts_sampling("claude-opus-5-5")
    assert not anthropic_accepts_sampling("claude-opus-4-8")
    assert not anthropic_accepts_sampling("claude-fable-5-1")
    assert anthropic_accepts_sampling("claude-sonnet-4-6")
    assert anthropic_accepts_sampling("claude-haiku-4-5")


def test_step_parses_tool_use_and_omits_temperature(api) -> None:  # noqa: ANN001
    api.replies.append(_message(
        [{"type": "text", "text": "Opening Finder."},
         {"type": "tool_use", "id": "tu_1", "name": "open_app", "input": {"name": "Finder"}}],
        stop_reason="tool_use",
    ))
    client = LLM(api_key="k", model="claude-sonnet-5", max_tokens=512, temperature=0)
    resp = client.step("sys", [{"role": "user", "content": "open finder"}],
                       [{"name": "open_app", "description": "d",
                         "input_schema": {"type": "object", "properties": {}}}])
    assert resp.tool_calls == [{"id": "tu_1", "name": "open_app", "input": {"name": "Finder"}}]
    assert resp.text == "Opening Finder."
    assert resp.stop_reason == "tool_use"
    assert (resp.input_tokens, resp.output_tokens) == (11, 7)
    sent = api.requests[0]
    assert sent["model"] == "claude-sonnet-5"
    assert sent["max_tokens"] == 512
    assert "temperature" not in sent
    assert sent["system"] == "sys"


def test_step_keeps_temperature_for_older_models(api) -> None:  # noqa: ANN001
    LLM(api_key="k", model="claude-sonnet-4-6", temperature=0).step(
        "sys", [{"role": "user", "content": "hi"}], [])
    assert api.requests[0]["temperature"] == 0


def test_analyze_image_sends_image_block(api, tmp_path) -> None:  # noqa: ANN001
    img = tmp_path / "shot.jpg"
    img.write_bytes(b"\xff\xd8\xff fake jpeg")
    api.replies.append(_message([{"type": "text", "text": "A Finder window."}]))
    out = LLM(api_key="k", model="claude-sonnet-5").analyze_image(str(img), "describe")
    assert out == "A Finder window."
    blocks = api.requests[0]["messages"][0]["content"]
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["media_type"] == "image/jpeg"
    assert blocks[1] == {"type": "text", "text": "describe"}


def test_stop_closes_the_in_flight_client(monkeypatch) -> None:  # noqa: ANN001
    """The HTTP client is registered with STOP and closed after the call."""
    closed: list[bool] = []

    class Tracking(httpx2.Client):
        def close(self) -> None:
            closed.append(True)
            super().close()

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_message([{"type": "text", "text": "ok"}]))

    monkeypatch.setattr(anthropic, "DefaultHttpxClient",
                        lambda **kw: Tracking(transport=httpx2.MockTransport(handler)))
    registered: list = []
    monkeypatch.setattr("aether.core.stop.register_http_closer", registered.append)
    import threading
    LLM(api_key="k", model="claude-sonnet-5").step(
        "s", [{"role": "user", "content": "x"}], [], abort_event=threading.Event())
    assert registered, "client was not registered for STOP cancellation"
    assert closed, "client was not closed after the call"
    assert llm_mod.DEFAULT_ANTHROPIC_MODEL == "claude-sonnet-5"
