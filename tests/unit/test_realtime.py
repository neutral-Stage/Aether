"""Realtime voice: GA session shape, tools (screen image before output, agent tasks), interrupt."""
from __future__ import annotations

import asyncio
import json

from aether.voice import realtime as rt


class FakeWS:
    def __init__(self, incoming: list[dict] | None = None) -> None:
        self.sent: list[dict] = []
        self.incoming = [json.dumps(e) for e in incoming or []]
        self.closed = False

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))

    def __aiter__(self):  # noqa: ANN204
        return self

    async def __anext__(self) -> str:
        if not self.incoming:
            await asyncio.sleep(3600)
        return self.incoming.pop(0)

    async def close(self) -> None:
        self.closed = True


def test_session_update_is_ga_push_to_talk() -> None:
    cfg = rt.RealtimeConfig(api_key="k")
    upd = cfg.session_update()["session"]
    assert upd["type"] == "realtime" and upd["audio"]["input"]["turn_detection"] is None
    assert upd["audio"]["output"]["voice"] == "marin"
    assert upd["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert [t["name"] for t in upd["tools"]] == ["look_at_screen", "do_task"]
    assert rt.normalize_event({"type": "response.audio.delta", "delta": "x"})["type"] \
        == "response.output_audio.delta"
    assert rt.normalize_event({"type": "error"}) == {"type": "error"}


def test_tools_screen_image_goes_before_the_output(tmp_path) -> None:  # noqa: ANN001
    png = tmp_path / "s.png"
    png.write_bytes(b"\x89PNG fake")
    goals: list[str] = []

    async def screen() -> str:
        return str(png)

    async def task(goal: str) -> str:
        goals.append(goal)
        return "Opened Downloads."

    async def scenario() -> FakeWS:
        ws = FakeWS([
            {"type": "response.function_call_arguments.done", "call_id": "c1",
             "name": "look_at_screen", "arguments": "{}"},
            {"type": "response.function_call_arguments.done", "call_id": "c2",
             "name": "do_task", "arguments": '{"goal": "open my downloads"}'},
            {"type": "response.function_call_arguments.done", "call_id": "c3",
             "name": "format_disk", "arguments": "not json"},
        ])
        s = rt.RealtimeSession(rt.RealtimeConfig(api_key="k"), screen_fn=screen, task_fn=task)
        seen: list[dict] = []
        s.on_event(seen.append)
        await s.connect(ws)
        await asyncio.sleep(0.1)
        await s.close()
        assert len(seen) == 3
        return ws

    ws = asyncio.run(scenario())
    kinds = [(m["type"], m.get("item", {}).get("type")) for m in ws.sent]
    assert kinds[0] == ("session.update", None)
    image = next(i for i, m in enumerate(ws.sent)
                 if m.get("item", {}).get("content", [{}])[0].get("type") == "input_image")
    out1 = next(i for i, m in enumerate(ws.sent) if m.get("item", {}).get("call_id") == "c1")
    assert image < out1
    assert ws.sent[image]["item"]["content"][0]["image_url"].startswith("data:image/png;base64,")
    outputs = {m["item"]["call_id"]: m["item"]["output"] for m in ws.sent
               if m.get("item", {}).get("type") == "function_call_output"}
    assert outputs["c2"] == "Opened Downloads." and goals == ["open my downloads"]
    assert outputs["c3"] == "Unknown tool format_disk."
    assert sum(1 for m in ws.sent if m["type"] == "response.create") == 3
    assert ws.closed


def test_missing_tools_and_failures_are_spoken() -> None:
    async def boom(goal: str) -> str:
        raise RuntimeError("no network")

    async def scenario() -> dict:
        ws = FakeWS()
        s = rt.RealtimeSession(rt.RealtimeConfig(api_key="k"), task_fn=boom)
        s._ws = ws  # noqa: SLF001
        await s.handle_tool_call({"call_id": "a", "name": "look_at_screen", "arguments": ""})
        await s.handle_tool_call({"call_id": "b", "name": "do_task",
                                  "arguments": '{"goal": "x"}'})
        await s.handle_tool_call({"call_id": "c", "name": "do_task", "arguments": "{}"})
        return {m["item"]["call_id"]: m["item"]["output"] for m in ws.sent
                if m.get("item", {}).get("type") == "function_call_output"}

    out = asyncio.run(scenario())
    assert "not available" in out["a"] and out["b"] == "That failed: no network"
    assert out["c"] == "No goal was given."


def test_audio_turns_and_interrupt() -> None:
    async def scenario() -> list[dict]:
        ws = FakeWS()
        s = rt.RealtimeSession(rt.RealtimeConfig(api_key="k"))
        s._ws = ws  # noqa: SLF001
        await s.send_audio_chunk(b"\x00\x01")
        await s.send_audio_chunk(b"")
        await s.commit_audio()
        await s.interrupt("item_9", 1234)
        await s.interrupt(None, 5)
        return ws.sent

    sent = asyncio.run(scenario())
    assert [m["type"] for m in sent] == [
        "input_audio_buffer.append", "input_audio_buffer.commit", "response.create",
        "response.cancel", "conversation.item.truncate", "response.cancel"]
    assert sent[4] == {"type": "conversation.item.truncate", "item_id": "item_9",
                       "content_index": 0, "audio_end_ms": 1234}


def test_config_from_env(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert rt.RealtimeConfig.from_env() is None
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = rt.RealtimeConfig.from_env(model=None, voice="cedar")
    assert (cfg.model, cfg.voice) == ("gpt-realtime", "cedar")
