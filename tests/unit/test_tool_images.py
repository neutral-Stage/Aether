"""Screenshots in tool results: Anthropic blocks, OpenAI-compatible follow-up
messages, text-only providers, pruning, and the agent loop attaching them."""
from __future__ import annotations

import asyncio
import base64

import pytest

from aether.core import llm
from aether.core.config import Config
from aether.core.llm import LLMResponse
from aether.core.orchestrator import Agent
from aether.core.router import RouteDecision, RouteTier
from aether.perception import screen


@pytest.fixture
def png(tmp_path) -> str:  # noqa: ANN001
    from PIL import Image

    path = tmp_path / "shot.png"
    Image.new("RGB", (8, 4), "red").save(path)
    disp = screen.DisplayInfo(1, 1, 0.0, 0.0, 8.0, 4.0, 1.0, True, True)
    screen._register(screen.Capture(str(path), disp, 8, 4, 1, True, "native"))  # noqa: SLF001
    return str(path)


def test_tool_result_carries_label_and_image(png) -> None:  # noqa: ANN001
    turn = llm.assistant_tool_results_turn([
        {"tool_use_id": "a", "content": "plain"},
        {"tool_use_id": "b", "content": "Screenshot attached", "images": [png]},
        {"tool_use_id": "c", "content": "gone", "images": ["/nonexistent.png"]},
    ])
    a, b, c = turn["content"]
    assert a["content"] == "plain" and c["content"] == "gone"
    kinds = [x["type"] for x in b["content"]]
    assert kinds == ["text", "text", "image"]
    assert "8x4 pixels" in b["content"][1]["text"]
    img = b["content"][2]["source"]
    assert img["media_type"] == "image/png"
    assert base64.standard_b64decode(img["data"]).startswith(b"\x89PNG")
    assert llm.tool_result_text(b) == "Screenshot attached\n" + b["content"][1]["text"]


def _conversation(png: str) -> list[dict]:
    return [
        {"role": "user", "content": "open the file"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "screenshot",
                                           "input": {}}]},
        llm.assistant_tool_results_turn([{"tool_use_id": "t1", "content": "attached",
                                          "images": [png]}]),
    ]


def test_openai_conversion_moves_images_to_a_user_message(png) -> None:  # noqa: ANN001
    oai = llm._anthropic_messages_to_openai("sys", _conversation(png))  # noqa: SLF001
    tool, follow = oai[-2], oai[-1]
    assert tool["role"] == "tool" and tool["tool_call_id"] == "t1"
    assert isinstance(tool["content"], str) and "attached" in tool["content"]
    assert follow["role"] == "user"
    assert follow["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_text_only_provider_gets_a_note_not_an_image(png) -> None:  # noqa: ANN001
    oai = llm._anthropic_messages_to_openai("sys", _conversation(png), images=False)  # noqa: SLF001
    assert "cannot see images" in oai[-1]["content"]
    assert "base64" not in str(oai)


def test_prune_keeps_newest_images(png) -> None:  # noqa: ANN001
    msgs: list[dict] = []
    for i in range(4):
        msgs.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "screenshot", "input": {}}]})
        msgs.append(llm.assistant_tool_results_turn(
            [{"tool_use_id": f"t{i}", "content": str(i), "images": [png]}]))
    assert llm.prune_images(msgs, keep=2) == 2
    kept = [m["content"][0]["content"][-1]["type"] for m in msgs if m["role"] == "user"]
    assert kept == ["text", "text", "image", "image"]
    assert llm.prune_images(msgs, keep=2) == 0


def test_block_dict_dumps_sdk_objects() -> None:
    class Block:
        def model_dump(self, **kw):  # noqa: ANN003, ANN202
            assert kw == {"mode": "json", "exclude_none": True}
            return {"type": "text", "text": "hi"}

    assert llm._block_dict(Block()) == {"type": "text", "text": "hi"}  # noqa: SLF001
    assert llm._block_dict({"type": "text"}) == {"type": "text"}  # noqa: SLF001


def test_provider_images_flag(monkeypatch) -> None:
    from aether.core import providers

    seen = {}

    class Fake:
        def __init__(self, **kw):  # noqa: ANN003
            seen.update(kw)

    monkeypatch.setattr(providers, "OpenAICompatibleClient", Fake)
    providers.create_client({"backend": "openai_compatible", "api_key_env": "X", "images": False},
                            api_keys={"X": "k"})
    assert seen["images"] is False


# ---- agent loop ---------------------------------------------------------------------

class Client:
    def __init__(self, script: list[tuple[str, dict]]) -> None:
        self.script = list(script)
        self.requests: list[list[dict]] = []

    def step(self, system, messages, tools, *, abort_event=None):  # noqa: ANN001
        import copy

        self.requests.append(copy.deepcopy(messages))
        name, args = self.script.pop(0) if self.script else ("finish", {"message": "done"})
        call = {"id": f"t{len(self.requests)}", "name": name, "input": args}
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


def _images_in(messages: list[dict]) -> int:
    n = 0
    for m in messages:
        if m["role"] != "user" or not isinstance(m["content"], list):
            continue
        for b in m["content"]:
            if isinstance(b.get("content"), list):
                n += sum(1 for c in b["content"] if c.get("type") == "image")
    return n


def test_agent_attaches_tool_images_and_prunes(agent, monkeypatch, png) -> None:  # noqa: ANN001
    def dispatch(name, args, ctx):  # noqa: ANN001, ANN202
        if name == "screenshot":
            ctx.pending_images.append(png)
        return "ok"

    monkeypatch.setattr(agent.registry, "dispatch", dispatch)
    client = Client([("screenshot", {})] * 3)
    monkeypatch.setattr(agent.router, "pick_client", lambda d: client)
    asyncio.run(agent.run_async("look", run_id="img"))
    assert [_images_in(r) for r in client.requests] == [0, 1, 2, 2]
    assert agent.ctx.pending_images == []


def test_agent_adds_verification_screenshot_when_ax_is_thin(agent, monkeypatch, png) -> None:  # noqa: ANN001
    def refresh(force=False):  # noqa: ANN001, ANN202
        agent.world.ax_insufficient = True
        return {}

    monkeypatch.setattr(agent.world, "refresh", refresh)
    monkeypatch.setattr(agent.registry, "dispatch", lambda n, a, c: "Clicked")
    monkeypatch.setattr(screen, "try_capture_to_file", lambda *a, **k: png)
    client = Client([("click", {"x": 1, "y": 2}), ("get_screen_context", {})])
    monkeypatch.setattr(agent.router, "pick_client", lambda d: client)
    asyncio.run(agent.run_async("click it", run_id="v"))
    # after the click: one screenshot; after the read-only call: none added
    assert [_images_in(r) for r in client.requests] == [0, 1, 1]
    assert agent.ctx.last_model_image == png


@pytest.mark.parametrize(("mode", "thin", "content", "ratio", "expect"), [
    ("auto", True, "unknown", 1.0, True),
    ("auto", False, "text_heavy", 0.05, True),     # canvas/Electron: text AX can't see
    ("auto", False, "ui", 0.05, False),            # toolbar-heavy but ordinary app
    ("auto", False, "text_heavy", 0.9, False),
    ("always", False, "ui", 0.9, True), ("off", True, "text_heavy", 0.0, False),
])
def test_verification_screenshot_modes(agent, monkeypatch, png, mode, thin, content,  # noqa: ANN001
                                       ratio, expect) -> None:
    agent.cfg.raw["agent"]["verify_screenshots"] = mode
    agent.world.ax_insufficient = thin
    agent.world.screen_content_class = content
    agent.world.ax_text_ratio = ratio
    monkeypatch.setattr(screen, "try_capture_to_file", lambda *a, **k: png)
    assert (agent._verification_screenshot() == png) is expect  # noqa: SLF001
