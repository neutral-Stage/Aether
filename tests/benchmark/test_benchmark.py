"""Benchmark harness tests (Phase 6)."""
from __future__ import annotations

import pytest

from tests.benchmark.scorer import load_tasks, run_mock_suite, run_repeat_suite, score_trace, summarize, summarize_repeat


@pytest.mark.benchmark
class TestBenchmarkHarness:
    def test_loads_ten_tasks(self) -> None:
        tasks = load_tasks()
        assert len(tasks) == 10

    def test_mock_suite_all_pass(self) -> None:
        results = run_mock_suite()
        summary = summarize(results)
        assert summary["total"] == 10
        assert summary["passed"] == 10
        assert summary["pass_rate_pct"] == 100.0

    def test_score_detects_missing_tool(self) -> None:
        task = {
            "id": "bad",
            "goal": "test",
            "success": {"required_tools": ["finish", "open_app"]},
        }
        result = score_trace(task, [{"tool": "open_app", "args": {}}])
        assert not result.passed
        assert "finish" in result.reason

    def test_repeat_suite_all_pass(self) -> None:
        comparisons = run_repeat_suite()
        summary = summarize_repeat(comparisons)
        assert summary["total"] == 10
        assert summary["skill_pass_rate_pct"] == 100.0


@pytest.mark.benchmark
class TestFleetBenchmark:
    def test_concurrent_sessions_all_reach(self) -> None:
        from tests.benchmark.fleet_bench import run_fleet_benchmark, summarize_fleet

        summary = summarize_fleet(run_fleet_benchmark(n=3, timeout=20.0))
        assert summary["total"] == 3
        assert summary["reached"] == 3
        assert summary["success_rate_pct"] == 100.0
        assert summary["total_cost_usd"] > 0  # cost rolled up from the CLI
