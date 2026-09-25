"""Operators (how to drive the app in front) and grounded clicks by description."""
from __future__ import annotations

import pytest

from aether.core.llm import LLMResponse
from aether.core.policy import Policy, PolicyConfig
from aether.effectors import operators
from aether.perception import accessibility as ax
from aether.perception import locate as locate_mod
from aether.perception import screen
from aether.tools import targeting_tools
from aether.tools.registry import DEFAULT_REGISTRY as R
from aether.tools.registry import AgentContext


def _els(n: int, role: str = "AXButton", labelled: bool = True) -> list[dict]:
    return [{"idx": i, "role": role, "title": f"B{i}" if labelled else "", "value": "",
             "x": 10 * i, "y": 10, "w": 8, "h": 8} for i in range(n)]


# ---- operators ------------------------------------------------------------------------------

def test_operator_choice() -> None:
    rich, thin = _els(12), _els(12, labelled=False)
    assert operators.choose("Google Chrome", rich, browser_attach_mode="cdp").primary.name \
        == "browser"
    assert operators.choose("Google Chrome", rich).primary.name == "ax"
    mail = operators.choose("Mail", rich, {"tier": 1, "scripting": {"x": "y"}})
    assert mail.primary.name == "script" and [o.name for o in mail.fallbacks] == ["ax", "vision"]
    assert [o.name for o in operators.choose("Mail", thin, {"tier": 1}).fallbacks] \
        == ["vision", "ax"]
    assert operators.choose("Visual Studio Code", rich, {"tier": 0}).primary.name == "code"
    figma = operators.choose("Figma", thin, {"tier": 2})
    assert figma.primary.name == "vision" and "only 0 labelled" in figma.reason
    logic = operators.choose("Logic Pro", rich, {"tier": 3})
    assert logic.primary.name == "ax" and logic.fallbacks[0].name == "vision"
    assert operators.choose("Unknown", []).reason == "nothing readable on screen"
    keynote = operators.choose("Keynote", thin, {"tier": 2, "scripting": {"a": "b"}})
    assert [o.name for o in keynote.fallbacks] == ["script", "ax"]
    text = mail.prompt("Mail")
    assert text.startswith("How to operate Mail: script (scriptable app).")
    assert "Reach for: run_applescript" in text and "If that fails: ax, then vision." in text


def test_ax_richness_counts_labelled_controls() -> None:
    mixed = _els(4) + _els(4, role="AXGroup") + _els(2, labelled=False)
    assert operators.ax_richness(mixed) == (4, 0.4)
    objs = [ax.Element(1, "AXButton", "OK", "", True, 0, 0, 10, 10)]
    assert operators.ax_richness(objs) == (1, 1.0)
    assert operators.ax_richness([]) == (0, 0.0)


# ---- locate ----------------------------------------------------------------------------------

def _cap(tmp_path) -> screen.Capture:  # noqa: ANN001
    from PIL import Image

    path = str(tmp_path / "full.png")
    Image.new("RGB", (2880, 1800), "white").save(path)
    disp = screen.DisplayInfo(1, 1, 0.0, 0.0, 1440.0, 900.0, 2.0, True, True)
    return screen._register(screen.Capture(path, disp, 2880, 1800, 1, True, "native"))  # noqa: SLF001


class Answers:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.questions: list[str] = []

    def step(self, system, messages, tools, *, abort_event=None):  # noqa: ANN001, ANN201
        assert system == locate_mod.LOCATE_SYSTEM
        self.questions.append(messages[0]["content"][-1]["text"])
        return LLMResponse(text=self.replies.pop(0), tool_calls=[], raw_content=[],
                           stop_reason="end_turn", backend="fake")


def test_locate_refines_and_snaps(tmp_path) -> None:  # noqa: ANN001
    cap = _cap(tmp_path)
    client = Answers('{"x": 1440, "y": 900}', '{"x": 320, "y": 300}')
    found = locate_mod.locate("the Export button", client, cap=cap, elements=[])
    assert (found.x, found.y, found.source) == (730.0, 450.0, "vision")
    assert "Where is: the Export button?" in client.questions[0]
    assert "close crop" in client.questions[1]
    button = ax.Element(3, "AXButton", "Export", "", True, 700, 440, 80, 24)
    snapped = locate_mod.locate("Export", Answers('{"x": 1440, "y": 900}', "none"), cap=cap,
                                elements=[button])
    assert (snapped.x, snapped.y, snapped.element_label) == (740.0, 452.0, "Export")
    far = locate_mod.locate("x", Answers('{"x": 1440, "y": 900}', '{"x": 0, "y": 0}'), cap=cap,
                            elements=[])
    assert (far.x, far.y) == (720.0, 450.0)          # the refine moved too far: ignored
    grid = locate_mod.locate("x", Answers('{"x": 500, "y": 500}'), cap=cap, refine=False,
                             coord_space="norm1000", elements=[], source="grounder")
    assert (grid.x, grid.y, grid.source) == (720.0, 450.0, "grounder")
    assert locate_mod.locate("x", Answers('{"x": null, "y": null}'), cap=cap) is None


def test_local_grounder_only_when_enabled_local_and_up(monkeypatch) -> None:  # noqa: ANN001
    settings = {"enabled": False, "base_url": "http://127.0.0.1:8080/v1", "model": "m"}
    monkeypatch.setattr(locate_mod, "grounder_settings", lambda: dict(settings))
    monkeypatch.setattr(locate_mod, "_answering", lambda url: True)
    assert locate_mod.local_grounder() is None
    settings["enabled"] = True
    client, space = locate_mod.local_grounder()
    assert space == "pixels" and client.model == "m"
    settings["coord_space"] = "norm1000"
    assert locate_mod.local_grounder()[1] == "norm1000"
    settings["base_url"] = "https://grounder.example.com/v1"
    assert locate_mod.local_grounder() is None
    settings["base_url"] = "http://localhost:8080/v1"
    monkeypatch.setattr(locate_mod, "_answering", lambda url: False)
    assert locate_mod.local_grounder() is None


def test_answering_probe_is_cached(monkeypatch) -> None:  # noqa: ANN001
    calls = []

    def fake_open(url, timeout):  # noqa: ANN001, ANN202
        calls.append(url)
        raise OSError("refused")

    monkeypatch.setattr(locate_mod.urllib.request, "urlopen", fake_open)
    locate_mod._probe_cache.clear()  # noqa: SLF001
    assert not locate_mod._answering("http://127.0.0.1:9/v1")  # noqa: SLF001
    assert not locate_mod._answering("http://127.0.0.1:9/v1")  # noqa: SLF001
    assert calls == ["http://127.0.0.1:9/v1/models"]


# ---- click_described and point_at(description=) ------------------------------------------------

def test_click_described(monkeypatch) -> None:  # noqa: ANN001
    clicks = []
    monkeypatch.setattr(targeting_tools, "_click_point", lambda x, y, args: clicks.append((x, y)))
    ctx = AgentContext()
    assert "only available inside an agent run" in R.dispatch(
        "click_described", {"description": "gear"}, ctx)
    ctx.locate = lambda d: locate_mod.Located(100.0, 200.0, "grounder")
    out = R.dispatch("click_described", {"description": "the gear icon"}, ctx)
    assert clicks == [(100.0, 200.0)] and "found by the local grounder" in out
    trash = ax.Element(1, "AXButton", "Empty Trash", "", True, 0, 0, 10, 10)
    ctx.locate = lambda d: locate_mod.Located(5.0, 5.0, "vision", trash)
    refused = R.dispatch("click_described", {"description": "the trash icon"}, ctx)
    assert refused.startswith("ERROR") and "Empty Trash" in refused and len(clicks) == 1
    ok = R.dispatch("click_described", {"description": "Empty Trash"}, ctx)
    assert "on 'Empty Trash'" in ok and len(clicks) == 2
    ctx.locate = lambda d: None
    assert "could not find" in R.dispatch("click_described", {"description": "x"}, ctx)
    ctx.locate = lambda d: locate_mod.Located(1.0, 2.0, "vision", trash)
    assert targeting_tools.resolve_point_target({"description": "trash"}, ctx) == \
        (1.0, 2.0, 10.0, 10.0, "Empty Trash")


def test_click_described_is_gated_like_a_click() -> None:
    spec = R.get("click_described")
    p = Policy(PolicyConfig())
    assert p.impact_of(spec, {"description": "the Buy Now button"}) == "destructive"
    assert p.impact_of(spec, {"description": "the gear icon"}) == "reversible"
    assert "Buy Now" in p.describe_operation(spec, {"description": "the Buy Now button"})


# ---- the agent -------------------------------------------------------------------------------

@pytest.fixture
def agent(minimal_config):  # noqa: ANN001, ANN201
    from aether.core.orchestrator import Agent

    minimal_config.raw["knowledge"] = {"enabled": True}
    return Agent(minimal_config, hud=None)


def test_prompt_names_the_operator(agent) -> None:  # noqa: ANN001
    agent.world.frontmost_app = "Mail"
    agent.world.elements = _els(12)
    agent.world.element_count = 12
    assert "How to operate Mail: script" in agent._system_prompt("archive old mail")  # noqa: SLF001
    agent.world.frontmost_app = ""
    assert agent._operator_line() == ""  # noqa: SLF001


def test_locate_prefers_the_local_grounder(agent, monkeypatch) -> None:  # noqa: ANN001
    calls = []

    def fake_locate(description, client, *, coord_space, source, max_edge):  # noqa: ANN001, ANN202
        calls.append((client, source))
        return None if source == "grounder" else locate_mod.Located(1, 2, source)

    monkeypatch.setattr(locate_mod, "locate", fake_locate)
    monkeypatch.setattr(locate_mod, "local_grounder", lambda: ("local-client", "pixels"))
    monkeypatch.setattr(agent.router, "pick_client_with_failover", lambda tier: "vision-client")
    found = agent.ctx.locate("the gear")
    assert found.source == "vision"
    assert calls == [("local-client", "grounder"), ("vision-client", "vision")]
    monkeypatch.setattr(locate_mod, "local_grounder", lambda: None)
    calls.clear()
    agent.ctx.locate("x")
    assert calls == [("vision-client", "vision")]
