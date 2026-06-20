"""Unit tests for explicit planner (Phase 11, FR-9)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from aether.core.planner import plan_goal, replan
from aether.core.world_model import WorldModel


@dataclass
class FakePlanResponse:
    text: str
    raw_content: list[Any] | None = None


class FakePlanLLM:
    def __init__(self, text: str) -> None:
        self._text = text

    def step(self, system: str, messages: list[dict], tools: list) -> FakePlanResponse:
        return FakePlanResponse(text=self._text)


@pytest.mark.unit
class TestPlanner:
    def test_heuristic_splits_then(self) -> None:
        world = WorldModel()
        world.set_goal("Open Finder then go to Downloads")
        result = plan_goal("Open Finder then go to Downloads", world, use_llm=False)
        assert len(result.steps) >= 2
        assert result.source == "heuristic"
        assert world.plan == result.steps

    def test_heuristic_numbered_list(self) -> None:
        world = WorldModel()
        goal = "1. Open Safari\n2. Go to apple.com\n3. Verify page"
        result = plan_goal(goal, world, use_llm=False)
        assert len(result.steps) == 3
        assert "Safari" in result.steps[0]

    def test_cached_plan_not_regenerated(self) -> None:
        world = WorldModel()
        world.set_plan(["Step A", "Step B"])
        world.is_novel_goal = False
        result = plan_goal("any goal", world, use_llm=False)
        assert result.source == "cached"
        assert result.steps == ["Step A", "Step B"]

    def test_llm_json_plan(self) -> None:
        world = WorldModel()
        llm = FakePlanLLM('["Open Mail", "Compose draft", "Finish"]')
        result = plan_goal("compose email", world, llm=llm, use_llm=True)
        assert result.source == "llm"
        assert result.steps[0] == "Open Mail"

    def test_replan_clears_old_plan(self) -> None:
        world = WorldModel()
        world.set_plan(["Old step"])
        world.needs_replan = True
        result = replan(
            "Open Finder and Downloads",
            world,
            use_llm=False,
            failure_context="verify failed",
        )
        assert result.steps != ["Old step"]
        assert world.plan == result.steps
