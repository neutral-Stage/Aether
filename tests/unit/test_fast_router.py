"""Fast router: strict matching, installed-app resolution, and the agent path."""
from __future__ import annotations

import asyncio

import pytest

from aether.core import fast_router as fr
from aether.core.config import Config
from aether.core.llm import LLMResponse
from aether.core.orchestrator import Agent
from aether.core.router import RouteDecision, RouteTier
from aether.effectors import system
from aether.tools.registry import DEFAULT_REGISTRY, AgentContext

APPS = {"safari": "Safari", "google chrome": "Google Chrome", "system settings": "System Settings",
        "mail": "Mail", "notes": "Notes", "finder": "Finder"}


@pytest.fixture(autouse=True)
def installed(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(fr, "installed_apps", lambda: dict(APPS))


@pytest.mark.parametrize(("goal", "tool", "args"), [
    ("Open Safari", "open_app", {"name": "Safari"}),
    ("please launch chrome", "open_app", {"name": "Google Chrome"}),
    ("Hey Aether, open settings.", "open_app", {"name": "System Settings"}),
    ("switch to Mail", "open_app", {"name": "Mail"}),
    ("quit Safari please", "quit_app", {"name": "Safari"}),
    ("close the Notes app", "quit_app", {"name": "Notes"}),
    ("show my downloads", "finder_go_to", {"path": "~/Downloads"}),
    ("open Documents folder", "finder_go_to", {"path": "~/Documents"}),
    ("go to the desktop", "finder_go_to", {"path": "~/Desktop"}),
    ("volume 30", "set_volume", {"level": 30}),
    ("set the volume to 150%", "set_volume", {"level": 100}),
    ("turn the volume down", "set_volume", {"change": -10}),
    ("louder", "set_volume", {"change": 10}),
    ("mute", "set_volume", {"muted": True}),
    ("unmute the sound", "set_volume", {"muted": False}),
])
def test_matches(goal: str, tool: str, args: dict) -> None:
    intent = fr.match(goal)
    assert intent is not None and (intent.tool, intent.args) == (tool, args)


@pytest.mark.parametrize("goal", ["what app is this?", "What's the frontmost app", "which app is this"])
def test_frontmost_question(goal: str) -> None:
    intent = fr.match(goal)
    assert intent is not None and intent.kind == "frontmost" and intent.tool is None


@pytest.mark.parametrize("goal", [
    "open my notes about the trip", "open the report I wrote yesterday", "open Figma",
    "Open Finder and confirm it is frontmost", "Go to Downloads folder in Finder",
    "close the window", "open google.com", "", "open " + "x" * 80,
])
def test_leaves_everything_else_to_the_agent(goal: str) -> None:
    assert fr.match(goal) is None


def test_installed_apps_scans_app_folders(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.undo()
    (tmp_path / "Foo Bar.app").mkdir()
    (tmp_path / "notes.txt").write_text("x")
    fr.installed_apps.cache_clear()
    monkeypatch.setattr(fr, "APP_DIRS", (str(tmp_path), str(tmp_path / "missing")))
    try:
        assert fr.installed_apps() == {"foo bar": "Foo Bar"}
    finally:
        fr.installed_apps.cache_clear()


# ---- tools ----------------------------------------------------------------------------

def test_volume_and_quit_scripts() -> None:
    assert system.volume_script(level=150).startswith("set volume output volume 100")
    assert "(v + (-10))" in system.volume_script(change=-10)
    assert system.volume_script(muted=True) == "set volume output muted true"
    assert system.quit_app_script('Evil" to do shell script "x') == \
        'tell application "Evil\\" to do shell script \\"x" to quit'


def test_new_tools_registered() -> None:
    for name in ("quit_app", "set_volume"):
        spec = DEFAULT_REGISTRY.get(name)
        assert spec is not None and (spec.permission, spec.impact) == ("input", "reversible")
    assert "ERROR" in DEFAULT_REGISTRY.dispatch("set_volume", {}, AgentContext())
    assert DEFAULT_REGISTRY.describe_call("set_volume", {"change": -10}) == "volume -10"


# ---- agent path -------------------------------------------------------------------------

class NoModel:
    calls = 0

    def step(self, system, messages, tools, *, abort_event=None):  # noqa: ANN001
        NoModel.calls += 1
        call = {"id": "t1", "name": "finish", "input": {"message": "model finished"}}
        return LLMResponse(text="", tool_calls=[call], raw_content=[{"type": "tool_use", **call}],
                           stop_reason="tool_use", backend="fake")


@pytest.fixture
def agent(minimal_config: Config, monkeypatch: pytest.MonkeyPatch) -> Agent:
    a = Agent(minimal_config, hud=None)
    monkeypatch.setattr(a.world, "refresh", lambda force=False: {})
    monkeypatch.setattr(a, "say", lambda text: None)
    monkeypatch.setattr(a.router, "route",
                        lambda *x, **k: RouteDecision(tier=RouteTier.CLOUD_FRONTIER, reason="t"))
    NoModel.calls = 0
    monkeypatch.setattr(a.router, "pick_client", lambda d: NoModel())
    return a


def _dispatch(monkeypatch, agent: Agent, result: str) -> list:  # noqa: ANN001
    seen: list = []
    monkeypatch.setattr(agent.registry, "dispatch",
                        lambda n, a, c: seen.append((n, a)) or result)
    return seen


def test_agent_fast_routes_without_a_model_call(agent, monkeypatch) -> None:  # noqa: ANN001
    seen = _dispatch(monkeypatch, agent, "Opened Safari")
    assert asyncio.run(agent.run_async("open safari", run_id="f")) == "Opened Safari"
    assert seen == [("open_app", {"name": "Safari"})] and NoModel.calls == 0


def test_failed_fast_route_falls_through_to_the_agent(agent, monkeypatch) -> None:  # noqa: ANN001
    _dispatch(monkeypatch, agent, "Failed to open Safari: not found")
    assert asyncio.run(agent.run_async("open safari", run_id="f")) == "model finished"
    assert NoModel.calls == 1


def test_careful_mode_and_disabled_use_the_agent(agent, monkeypatch) -> None:  # noqa: ANN001
    seen = _dispatch(monkeypatch, agent, "Opened Safari")
    agent.policy.config.careful = True
    asyncio.run(agent.run_async("open safari", run_id="c"))
    agent.policy.config.careful = False
    agent.cfg.raw["agent"]["fast_router"] = False
    asyncio.run(agent.run_async("open safari", run_id="d"))
    assert NoModel.calls == 2 and seen == []


def test_frontmost_answer(agent, monkeypatch) -> None:  # noqa: ANN001
    def refresh(force=False):  # noqa: ANN001, ANN202
        agent.world.frontmost_app = "Preview"
        return {}

    monkeypatch.setattr(agent.world, "refresh", refresh)
    assert asyncio.run(agent.run_async("what app is this?", run_id="w")) == "This is Preview."
    assert NoModel.calls == 0
