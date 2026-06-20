"""Orchestrator smoke tests — construction and wiring (Phase 6)."""
from __future__ import annotations

import pytest

from aether.core.config import Config
from aether.core.orchestrator import Agent, BASE_SYSTEM_PROMPT


@pytest.mark.unit
class TestOrchestratorSmoke:
    def test_base_system_prompt_non_empty(self) -> None:
        assert "Aether" in BASE_SYSTEM_PROMPT
        assert "get_screen_context" in BASE_SYSTEM_PROMPT

    def test_agent_constructs_with_minimal_config(self, minimal_config: Config) -> None:
        agent = Agent(minimal_config, hud=None)
        assert agent.world is not None
        assert agent.router is not None
        assert agent.registry is not None
        assert agent.policy is not None
        assert agent.ctx.world is agent.world

    def test_system_prompt_includes_goal_context(self, minimal_config: Config) -> None:
        agent = Agent(minimal_config, hud=None)
        agent.world.set_goal("Open Finder")
        agent.world.frontmost_app = "Finder"
        prompt = agent._system_prompt("Open Finder")  # noqa: SLF001
        assert "Finder" in prompt or "Open Finder" in prompt
