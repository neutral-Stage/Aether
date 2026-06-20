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
