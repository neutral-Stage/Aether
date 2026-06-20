"""Local observability metrics (§NFR-8, Phase 4–5).

Thread-safe counters and latency histograms exposed via sidecar GET /metrics.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# Latency budgets (ms) — warn when exceeded (Phase 5)
LATENCY_BUDGETS_MS: dict[str, float] = {
    "ax_refresh_ms": 50.0,
    "step_latency_ms": 3000.0,
    "tool_latency_ms": 2000.0,
    "percept_refresh_ms": 100.0,
    "stop_latency_ms": 200.0,
}

# Estimated LLM prices in USD per 1K tokens (input, output). ESTIMATES ONLY —
# token counts from providers can differ from billed amounts; cache/tiered
# pricing is ignored. Keyed by provider (LLMResponse.backend); MODEL_PRICES
# overrides per model where known. local_http is free.
PROVIDER_PRICES: dict[str, dict[str, float]] = {
    "anthropic": {"in": 0.003, "out": 0.015},
    "openai": {"in": 0.00015, "out": 0.0006},
    "google": {"in": 0.0001, "out": 0.0004},
    "groq": {"in": 0.00059, "out": 0.00079},
    "fireworks": {"in": 0.0009, "out": 0.0009},
    "openrouter": {"in": 0.003, "out": 0.015},
    "kilo": {"in": 0.003, "out": 0.015},
    "kie": {"in": 0.00125, "out": 0.005},
    "zai": {"in": 0.0006, "out": 0.0022},
    "zai_vision": {"in": 0.0006, "out": 0.0022},
    "local_http": {"in": 0.0, "out": 0.0},
}

MODEL_PRICES: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"in": 0.003, "out": 0.015},
    "gpt-4o-mini": {"in": 0.00015, "out": 0.0006},
    "gemini-2.0-flash": {"in": 0.0001, "out": 0.0004},
    "llama-3.3-70b-versatile": {"in": 0.00059, "out": 0.00079},
    "glm-5-turbo": {"in": 0.0006, "out": 0.0022},
    "glm-5v-turbo": {"in": 0.0006, "out": 0.0022},
}


def estimate_cost_usd(
    provider: str,
    model: str | None,
    tokens_in: int,
    tokens_out: int,
) -> float:
    """Estimated USD cost for a turn. 0.0 for local/unknown providers."""
    rate = None
    if model and model in MODEL_PRICES:
        rate = MODEL_PRICES[model]
    elif provider in PROVIDER_PRICES:
        rate = PROVIDER_PRICES[provider]
    if rate is None:
        return 0.0
    return (tokens_in / 1000.0) * rate["in"] + (tokens_out / 1000.0) * rate["out"]


@dataclass
class RunMetrics:
    run_id: str
    goal: str
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    status: str = "running"
    steps: int = 0
    tool_calls: int = 0
    errors: int = 0
    route_tiers: dict[str, int] = field(default_factory=dict)
    step_latencies_ms: list[float] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


class MetricsCollector:
    """Singleton metrics store for orchestrator + sidecar."""

    _instance: MetricsCollector | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._mutex = threading.RLock()  # re-entrant: record_step/start_run call inc()/observe() while held
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._runs: list[RunMetrics] = []
        self._current_run: RunMetrics | None = None
        self._max_runs = 50
        self._max_hist = 200
        self._provider_costs: dict[str, dict[str, float]] = {}

    @classmethod
    def get(cls) -> MetricsCollector:
        with cls._lock:
            if cls._instance is None:
                cls._instance = MetricsCollector()
            return cls._instance

    def inc(self, name: str, value: int = 1) -> None:
        with self._mutex:
            self._counters[name] += value

    def set_gauge(self, name: str, value: float) -> None:
        with self._mutex:
            self._gauges[name] = value

    def observe(self, name: str, value_ms: float) -> None:
        with self._mutex:
            bucket = self._histograms[name]
            bucket.append(value_ms)
            if len(bucket) > self._max_hist:
                del bucket[: len(bucket) - self._max_hist]

    def warn_if_slow(self, name: str, value_ms: float) -> None:
        budget = LATENCY_BUDGETS_MS.get(name)
        if budget is None or value_ms <= budget:
            return
        with self._mutex:
            self._counters["slow_path_warnings"] += 1
            self._counters[f"slow_{name}"] += 1

    def start_run(self, run_id: str, goal: str) -> None:
        with self._mutex:
            self._current_run = RunMetrics(run_id=run_id, goal=goal)
            self.inc("runs_started")

    def end_run(self, status: str) -> None:
        with self._mutex:
            if self._current_run is None:
                return
            self._current_run.finished_at = time.time()
            self._current_run.status = status
            self._runs.append(self._current_run)
            if len(self._runs) > self._max_runs:
                self._runs = self._runs[-self._max_runs :]
            self._current_run = None
            self.inc(f"runs_{status}")

    def record_step(self, route_tier: str, latency_ms: float) -> None:
        with self._mutex:
            if self._current_run:
                self._current_run.steps += 1
                self._current_run.step_latencies_ms.append(latency_ms)
                self._current_run.route_tiers[route_tier] = (
                    self._current_run.route_tiers.get(route_tier, 0) + 1
                )
            self.inc("steps_total")
            self.observe("step_latency_ms", latency_ms)
            self.inc(f"route_{route_tier}")

    def record_tool(self, name: str, latency_ms: float, error: bool = False) -> None:
        with self._mutex:
            if self._current_run:
                self._current_run.tool_calls += 1
                if error:
                    self._current_run.errors += 1
            self.inc("tool_calls_total")
            self.inc(f"tool_{name}")
            if error:
                self.inc("tool_errors_total")
            self.observe("tool_latency_ms", latency_ms)

    def record_llm_usage(
        self,
        provider: str,
        tokens_in: int | None,
        tokens_out: int | None,
        model: str | None = None,
    ) -> None:
        if tokens_in is None and tokens_out is None:
            return
        ti = int(tokens_in or 0)
        to = int(tokens_out or 0)
        known = provider in PROVIDER_PRICES or (model is not None and model in MODEL_PRICES)
        cost = estimate_cost_usd(provider, model, ti, to)
        self.inc(f"tokens_in_{provider}", ti)
        self.inc(f"tokens_out_{provider}", to)
        if not known:
            self.inc("unknown_provider_usage")
        with self._mutex:
            entry = self._provider_costs.setdefault(
                provider, {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "calls": 0}
            )
            entry["tokens_in"] += ti
            entry["tokens_out"] += to
            entry["cost_usd"] += cost
            entry["calls"] += 1
            if self._current_run:
                self._current_run.tokens_in += ti
                self._current_run.tokens_out += to
                self._current_run.cost_usd += cost

    def snapshot(self) -> dict[str, Any]:
        with self._mutex:
            runs = []
            for r in self._runs[-20:]:
                duration = None
                if r.finished_at:
                    duration = round((r.finished_at - r.started_at) * 1000, 1)
                runs.append({
                    "run_id": r.run_id,
                    "goal": r.goal[:80],
                    "status": r.status,
                    "steps": r.steps,
                    "tool_calls": r.tool_calls,
                    "errors": r.errors,
                    "route_tiers": r.route_tiers,
                    "duration_ms": duration,
                    "avg_step_ms": (
                        round(sum(r.step_latencies_ms) / len(r.step_latencies_ms), 1)
                        if r.step_latencies_ms
                        else None
                    ),
                })
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {
                    k: {
                        "count": len(v),
                        "p50": _percentile(v, 50),
                        "p95": _percentile(v, 95),
                        "max": round(max(v), 1) if v else None,
                    }
                    for k, v in self._histograms.items()
                },
                "current_run": (
                    {
                        "run_id": self._current_run.run_id,
                        "goal": self._current_run.goal[:80],
                        "steps": self._current_run.steps,
                    }
                    if self._current_run
                    else None
                ),
                "recent_runs": runs,
                "latency_budgets_ms": LATENCY_BUDGETS_MS,
                "slow_warnings": self._counters.get("slow_path_warnings", 0),
                "provider_costs": {
                    p: dict(v) for p, v in self._provider_costs.items()
                },
                "total_cost_usd": round(
                    sum(v["cost_usd"] for v in self._provider_costs.values()), 6
                ),
            }


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = int(len(ordered) * pct / 100)
    idx = min(idx, len(ordered) - 1)
    return round(ordered[idx], 1)
