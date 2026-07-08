"""Live fleet benchmark against the fake claude CLI (Phase 6, no API keys).

Spawns N concurrent ClaudeCodeSessions (real subprocesses, deterministic
fixture) and measures spawn→first-result success rate + wall-clock latency +
rolled-up cost. CI-safe: no keys, no macOS deps.

Run standalone: python -m tests.benchmark.fleet_bench
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from aether.fleet.claude_session import ClaudeCodeSession

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_claude.py"
_TERMINAL = ("awaiting_input", "done", "error")


@dataclass
class FleetResult:
    session_id: str
    reached: bool
    latency_ms: float
    cost_usd: float


def _wait(cond, timeout: float, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(interval)
    return False


def run_fleet_benchmark(
    n: int = 4, workspace: str | None = None, timeout: float = 15.0,
) -> list[FleetResult]:
    os.chmod(FIXTURE, 0o755)
    ws = workspace or str(FIXTURE.parent)
    sessions = [
        ClaudeCodeSession(
            binary=str(FIXTURE), session_id=f"bench-{i}", label=f"bench-{i}",
            agent_type="claude", prompt="benchmark task", workspace=ws,
        )
        for i in range(n)
    ]
    started_at: dict[str, float] = {}
    for s in sessions:  # start all concurrently
        started_at[s.session_id] = time.monotonic()
        s.start()

    results: list[FleetResult] = []
    for s in sessions:
        reached = _wait(
            lambda s=s: s.state in _TERMINAL and bool(s.result_summary),
            timeout=timeout,
        )
        latency_ms = (time.monotonic() - started_at[s.session_id]) * 1000
        results.append(FleetResult(
            session_id=s.session_id, reached=reached,
            latency_ms=latency_ms, cost_usd=s.cost_usd))
    for s in sessions:
        s.stop(grace_sec=0.5)
    return results


def summarize_fleet(results: list[FleetResult]) -> dict:
    n = len(results)
    ok = sum(1 for r in results if r.reached)
    lat = sorted(r.latency_ms for r in results)

    def pct(p: float) -> float:
        if not lat:
            return 0.0
        return round(lat[min(len(lat) - 1, int(p / 100 * len(lat)))], 1)

    return {
        "total": n,
        "reached": ok,
        "success_rate_pct": round(100.0 * ok / n, 1) if n else 0.0,
        "latency_p50_ms": pct(50),
        "latency_p95_ms": pct(95),
        "total_cost_usd": round(sum(r.cost_usd for r in results), 4),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(summarize_fleet(run_fleet_benchmark()), indent=2))
