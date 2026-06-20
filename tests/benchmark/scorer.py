"""Benchmark task scoring utilities (Phase 6, Phase 11 memory/skill metrics)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

BENCHMARK_TASKS_PATH = Path(__file__).resolve().parent / "tasks.yaml"

# Phase 11 baseline (mock suite, June 2026)
BASELINE_MOCK_PASS_RATE_PCT = 100.0
BASELINE_REPEAT_SKILL_BOOST_PCT = 0.0  # skill replay matches mock traces in CI


@dataclass
class TaskResult:
    task_id: str
    goal: str
    passed: bool
    reason: str
    tools_used: list[str] = field(default_factory=list)
    skill_assisted: bool = False


@dataclass
class RepeatComparison:
    task_id: str
    baseline_passed: bool
    repeat_passed: bool
    skill_trace_passed: bool
    memory_boost: bool


def load_tasks(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or BENCHMARK_TASKS_PATH
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return list(data.get("tasks") or [])


def score_trace(
    task: dict[str, Any],
    trace: list[dict[str, Any]],
    *,
    skill_assisted: bool = False,
) -> TaskResult:
    """Score a tool trace against task success criteria."""
    task_id = str(task.get("id", "unknown"))
    goal = str(task.get("goal", ""))
    success = task.get("success") or {}
    required = list(success.get("required_tools") or [])
    min_steps = int(success.get("min_steps") or len(required))

    tools_used = [str(step.get("tool", "")) for step in trace if step.get("tool")]
    missing = [t for t in required if t not in tools_used]

    if missing:
        return TaskResult(
            task_id=task_id,
            goal=goal,
            passed=False,
            reason=f"missing tools: {', '.join(missing)}",
            tools_used=tools_used,
            skill_assisted=skill_assisted,
        )
    if len(tools_used) < min_steps:
        return TaskResult(
            task_id=task_id,
            goal=goal,
            passed=False,
            reason=f"too few steps: {len(tools_used)} < {min_steps}",
            tools_used=tools_used,
            skill_assisted=skill_assisted,
        )
    return TaskResult(
        task_id=task_id,
        goal=goal,
        passed=True,
        reason="ok",
        tools_used=tools_used,
        skill_assisted=skill_assisted,
    )


def score_repeat_task(task: dict[str, Any]) -> RepeatComparison:
    """Compare first-run baseline vs skill-assisted repeat trace (Phase 11)."""
    baseline = score_trace(task, list(task.get("mock_trace") or []))
    repeat = score_trace(
        task,
        list(task.get("mock_repeat_trace") or task.get("mock_trace") or []),
    )
    skill = score_trace(
        task,
        list(task.get("mock_skill_trace") or task.get("mock_trace") or []),
        skill_assisted=True,
    )
    memory_boost = skill.passed and (not baseline.passed or skill.tools_used <= baseline.tools_used)
    return RepeatComparison(
        task_id=str(task.get("id", "")),
        baseline_passed=baseline.passed,
        repeat_passed=repeat.passed,
        skill_trace_passed=skill.passed,
        memory_boost=memory_boost,
    )


def run_mock_suite(path: Path | None = None) -> list[TaskResult]:
    """Score each task's mock_trace — no LLM or sidecar required."""
    results: list[TaskResult] = []
    for task in load_tasks(path):
        trace = list(task.get("mock_trace") or [])
        results.append(score_trace(task, trace))
    return results


def run_repeat_suite(path: Path | None = None) -> list[RepeatComparison]:
    """Score repeat/skill-assisted traces for memory improvement metrics."""
    return [score_repeat_task(task) for task in load_tasks(path)]


def summarize(results: list[TaskResult]) -> dict[str, Any]:
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    skill_passed = sum(1 for r in results if r.passed and r.skill_assisted)
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate_pct": round(100.0 * passed / total, 1) if total else 0.0,
        "skill_assisted_passed": skill_passed,
        "results": [
            {
                "id": r.task_id,
                "passed": r.passed,
                "reason": r.reason,
                "tools": r.tools_used,
                "skill_assisted": r.skill_assisted,
            }
            for r in results
        ],
    }


def summarize_repeat(comparisons: list[RepeatComparison]) -> dict[str, Any]:
    total = len(comparisons)
    repeat_ok = sum(1 for c in comparisons if c.repeat_passed)
    skill_ok = sum(1 for c in comparisons if c.skill_trace_passed)
    boosted = sum(1 for c in comparisons if c.memory_boost)
    return {
        "total": total,
        "repeat_passed": repeat_ok,
        "skill_trace_passed": skill_ok,
        "memory_boost_count": boosted,
        "repeat_pass_rate_pct": round(100.0 * repeat_ok / total, 1) if total else 0.0,
        "skill_pass_rate_pct": round(100.0 * skill_ok / total, 1) if total else 0.0,
        "baseline_mock_pass_rate_pct": BASELINE_MOCK_PASS_RATE_PCT,
        "results": [
            {
                "id": c.task_id,
                "baseline_passed": c.baseline_passed,
                "repeat_passed": c.repeat_passed,
                "skill_trace_passed": c.skill_trace_passed,
                "memory_boost": c.memory_boost,
            }
            for c in comparisons
        ],
    }
