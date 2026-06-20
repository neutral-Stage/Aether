"""Unit tests for world model verify-after-act (Phase 6)."""
from __future__ import annotations

import pytest

from aether.core.world_model import VerificationExpectation, WorldModel


@pytest.mark.unit
class TestWorldModelVerify:
    def test_verify_passes_with_expected_app(
        self,
        world: WorldModel,
        mock_ax_screen: dict,
    ) -> None:
        world.begin_action_verification(
            VerificationExpectation(app_name="Safari")
        )
        ok = world.verify(None, "opened Safari")
        assert ok is True
        assert world.step_failure_count == 0
        assert not world.needs_replan

    def test_verify_fails_wrong_app(
        self,
        world: WorldModel,
        mock_ax_screen: dict,
    ) -> None:
        mock_ax_screen["frontmost_app"] = "Mail"
        world.begin_action_verification(
            VerificationExpectation(app_name="Safari")
        )
        ok = world.verify(None, "tried to open Safari")
        assert ok is False
        assert world.step_failure_count == 1
        assert world.needs_replan

    def test_verify_legacy_contains_check(self, world: WorldModel) -> None:
        world._pre_action = None  # noqa: SLF001
        world._pending_expectation = None  # noqa: SLF001
        ok = world.verify({"contains": "success"}, "Task completed with success")
        assert ok is True

    def test_snapshot_includes_goal(self, world: WorldModel) -> None:
        snap = world.snapshot()
        assert snap["goal"] == "test goal"
        assert snap["element_count"] == 5

    def test_record_tool_call_trace(self, world: WorldModel) -> None:
        world.record_tool_call("open_app", {"name": "Finder"})
        trace = world.tool_trace()
        assert len(trace) == 1
        assert trace[0]["tool"] == "open_app"
