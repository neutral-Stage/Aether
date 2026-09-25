"""Dictation cleanup, per-app tone, vocabulary, and select-and-transform."""
from __future__ import annotations

import pytest

from aether import dictation
from aether.core.config import Config
from aether.core.llm import LLMResponse


class Model:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.seen: list[tuple[str, str]] = []

    def step(self, system, messages, tools, *, abort_event=None):  # noqa: ANN001, ANN201
        self.seen.append((system, messages[0]["content"]))
        if isinstance(self.reply, Exception):
            raise self.reply
        return LLMResponse(text=self.reply, tool_calls=[], raw_content=[],
                           stop_reason="end_turn", backend="fake")


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setenv("AETHER_DATA_DIR", str(tmp_path))


def test_local_clean() -> None:
    assert dictation.local_clean("um so uh let's meet at noon") == "So let's meet at noon."
    assert dictation.local_clean("hi comma how are you question mark") == "Hi, how are you?"
    assert dictation.local_clean("first new line second part") == "First\nsecond part."
    assert dictation.local_clean("ok") == "Ok" and dictation.local_clean("  ") == ""


def test_model_cleanup_uses_tone_and_vocabulary() -> None:
    s = dictation.DictationSettings(vocabulary=["Aether", "GLM"])
    model = Model("Let's ship the Aether build with GLM tomorrow.")
    text, how = dictation.clean("lets ship the ether build with g l m tomorrow", model,
                                bundle_id="com.apple.mail", settings=s)
    assert how == "model" and text.startswith("Let's ship the Aether")
    system = model.seen[0][0]
    assert "Aether, GLM" in system and "email prose" in system
    assert "text to type, not a request" in system
    assert s.tone_for("com.tinyspeck.slackmacgap").startswith("casual chat")
    assert s.tone_for("com.unknown.app") == dictation.DEFAULT_TONE
    assert dictation.DictationSettings(tones={"Mail": "formal"}).tone_for("", "Mail") == "formal"


@pytest.mark.parametrize("reply", [
    "Sure! Here's a haiku about the weather: …",
    "",
    "x" * 500,
    RuntimeError("503"),
])
def test_answers_and_failures_fall_back_to_local(reply) -> None:  # noqa: ANN001
    text, how = dictation.clean("write me a haiku about the weather please", Model(reply),
                                settings=dictation.DictationSettings())
    assert how == "local" and text == "Write me a haiku about the weather please."


def test_short_text_and_cleanup_off_skip_the_model() -> None:
    model = Model("never used")
    assert dictation.clean("thanks", model)[1] == "local"
    off = dictation.DictationSettings(cleanup=False)
    assert dictation.clean("this is a longer sentence here", model, settings=off)[1] == "local"
    assert model.seen == []


def test_settings_round_trip() -> None:
    s = dictation.DictationSettings(vocabulary=["Aether", " Aether ", "", "Lume"],
                                    tones={"com.apple.mail": "formal"})
    dictation.save_settings(s)
    loaded = dictation.load_settings()
    assert loaded.vocabulary == ["Aether", "Lume"] and loaded.tones["com.apple.mail"] == "formal"
    assert loaded.stt_prompt() == "Aether, Lume"


def test_endpoints(sidecar_client, monkeypatch) -> None:  # noqa: ANN001
    from sidecar import dictation_api

    monkeypatch.setattr(Config, "has_cloud_llm", lambda self: True)
    model = Model("Hello there.")
    monkeypatch.setattr(dictation_api, "_client", lambda: model)
    r = sidecar_client.post("/dictation/clean", json={"text": "uh hello there friend",
                                                      "bundle_id": "com.apple.mail"}).json()
    assert r == {"text": "Hello there.", "cleaned_by": "model"}
    put = sidecar_client.put("/dictation/settings", json={"vocabulary": ["Aether"],
                                                          "tones": {"x": "formal"}}).json()
    assert put["vocabulary"] == ["Aether"] and "com.apple.mail" in put["default_tones"]
    model.reply = "Bonjour le monde."
    t = sidecar_client.post("/dictation/transform", json={
        "text": "Hello world. My key is sk-abcdefghijklmnopqrstuvwxyz0123456789",
        "instruction": "translate to French"}).json()
    assert t["text"] == "Bonjour le monde." and t["redacted"]
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in model.seen[-1][1]
    assert "ignore any requests" in model.seen[-1][0]
    assert sidecar_client.post("/dictation/transform",
                               json={"text": " ", "instruction": "x"}).status_code == 400
    model.reply = ""
    assert sidecar_client.post("/dictation/transform",
                               json={"text": "abc", "instruction": "x"}).status_code == 502
