"""Regression: composed MetricsCollector methods must not deadlock (Phase 1)."""
from __future__ import annotations

import pytest

from aether.core.metrics import MetricsCollector


@pytest.mark.unit
class TestMetricsNoDeadlock:
    def test_record_step_completes(self) -> None:
        m = MetricsCollector()
        m.record_step("local_fast", 12.3)  # hangs today (re-entrant Lock)
        assert m.snapshot()["counters"]["steps_total"] == 1

    def test_run_lifecycle_completes(self) -> None:
        m = MetricsCollector()
        m.start_run("r1", "goal")
        m.record_step("cloud_frontier", 5.0)
        m.record_tool("click", 3.0)
        m.end_run("completed")
        snap = m.snapshot()
        assert snap["counters"]["runs_started"] == 1
        assert snap["counters"]["runs_completed"] == 1
