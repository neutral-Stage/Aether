"""Suggestion chips: fail-closed parsing and the endpoint."""
from __future__ import annotations

from aether.core.config import Config
from aether.core.llm import LLMResponse
from sidecar import chips_api


def test_parse_chips_is_strict() -> None:
    text = """Here you go: {"chips": [
      {"label": "Explain this setting", "kind": "understand", "prompt": "Explain Private Relay"},
      {"label": "Shorten it", "kind": "transform", "prompt": "Make the selection shorter"},
      {"label": "", "kind": "execute", "prompt": "x"},
      {"label": "Format disk", "kind": "destroy", "prompt": "x"},
      {"label": "Turn it on", "kind": "Execute", "prompt": "Turn on Private Relay"},
      {"label": "Ideas", "kind": "ideate", "prompt": "Ideas for privacy"}]}"""
    got = chips_api.parse_chips(text, has_selection=False)
    assert [c["kind"] for c in got] == ["understand", "execute", "ideate"]
    assert len(chips_api.parse_chips(text, has_selection=True)) == 3
    assert chips_api.parse_chips(text, has_selection=True)[1]["kind"] == "transform"
    for bad in ("", "no json", '{"chips": "nope"}', "[1,2]", '{"chips": [1, 2]}'):
        assert chips_api.parse_chips(bad, has_selection=True) == []


def test_endpoint(sidecar_client, monkeypatch) -> None:  # noqa: ANN001
    from sidecar import talk_api

    seen = []

    class Model:
        def step(self, system, messages, tools, *, abort_event=None):  # noqa: ANN001, ANN201
            seen.append(messages[0]["content"])
            return LLMResponse(text='{"chips": [{"label": "Explain it", "kind": "understand", '
                                    '"prompt": "Explain the Wi-Fi toggle"}]}',
                               tool_calls=[], raw_content=[], stop_reason="end_turn",
                               backend="fake")

    monkeypatch.setattr(Config, "has_cloud_llm", lambda self: True)
    monkeypatch.setattr(talk_api, "_client", lambda cfg: Model())
    monkeypatch.setattr(chips_api, "_context", lambda x, y, sel: f"Under the pointer: Wi-Fi. {sel}")
    r = sidecar_client.post("/chips", json={"x": 1, "y": 2,
                                            "selection": "pw sk-abcdefghijklmnopqrstuvwxyz0123456789"})
    data = r.json()
    assert data["chips"] == [{"label": "Explain it", "kind": "understand",
                              "prompt": "Explain the Wi-Fi toggle"}]
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in seen[0]
