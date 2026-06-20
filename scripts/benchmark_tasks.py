#!/usr/bin/env python3
"""Automated task benchmark runner (Phase 6).

Mock mode (default, CI-safe):
  python scripts/benchmark_tasks.py --mock

Live sidecar mode (requires running sidecar + API keys):
  python scripts/benchmark_tasks.py --sidecar http://127.0.0.1:8765 --token $AETHER_SIDECAR_TOKEN
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.benchmark.scorer import (  # noqa: E402
    BENCHMARK_TASKS_PATH,
    load_tasks,
    run_mock_suite,
    run_repeat_suite,
    score_trace,
    summarize,
    summarize_repeat,
)


def run_live(sidecar_url: str, token: str | None) -> dict:
    """Placeholder live runner — records tasks as manual/pending until wired."""
    import urllib.error
    import urllib.request

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    tasks = load_tasks()
    results = []
    for task in tasks:
        payload = json.dumps(
            {"goal": task["goal"], "stream": False, "local_only": False}
        ).encode()
        req = urllib.request.Request(
            f"{sidecar_url.rstrip('/')}/run",
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                body = json.loads(resp.read().decode())
            passed = body.get("status") == "idle" and body.get("error") is None
            results.append(
                {
                    "id": task["id"],
                    "passed": passed,
                    "reason": body.get("error") or body.get("result") or "ok",
                    "tools": [],
                }
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            results.append(
                {
                    "id": task["id"],
                    "passed": False,
                    "reason": f"HTTP {exc.code}: {detail[:200]}",
                    "tools": [],
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "id": task["id"],
                    "passed": False,
                    "reason": str(exc),
                    "tools": [],
                }
            )

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate_pct": round(100.0 * passed / total, 1) if total else 0.0,
        "results": results,
        "mode": "live",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Aether task benchmark harness")
    parser.add_argument(
        "--tasks",
        type=Path,
        default=BENCHMARK_TASKS_PATH,
        help="Path to tasks.yaml",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Score mock traces only (no sidecar)",
    )
    parser.add_argument(
        "--sidecar",
        type=str,
        default="",
        help="Sidecar base URL for live runs",
    )
    parser.add_argument("--token", type=str, default="", help="Bearer token if configured")
    parser.add_argument(
        "--repeat",
        action="store_true",
        help="Score repeat/skill-assisted traces (Phase 11)",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args()

    if args.sidecar:
        summary = run_live(args.sidecar, args.token or None)
    elif args.repeat:
        comparisons = run_repeat_suite(args.tasks)
        summary = summarize_repeat(comparisons)
        summary["mode"] = "repeat"
    else:
        results = run_mock_suite(args.tasks)
        summary = summarize(results)
        summary["mode"] = "mock"

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        if summary.get("mode") == "repeat":
            print(
                f"Benchmark (repeat): skill {summary['skill_pass_rate_pct']}% · "
                f"repeat {summary['repeat_pass_rate_pct']}% · "
                f"boosts {summary['memory_boost_count']}/{summary['total']}"
            )
            for row in summary["results"]:
                mark = "PASS" if row["skill_trace_passed"] else "FAIL"
                boost = " ↑" if row.get("memory_boost") else ""
                print(f"  [{mark}] {row['id']}{boost}")
        else:
            print(
                f"Benchmark ({summary['mode']}): {summary['passed']}/{summary['total']} passed "
                f"({summary['pass_rate_pct']}%)"
            )
            for row in summary["results"]:
                mark = "PASS" if row["passed"] else "FAIL"
                print(f"  [{mark}] {row['id']}: {row['reason']}")

    failed = summary.get("failed")
    if failed is None:
        failed = summary.get("total", 0) - summary.get("skill_trace_passed", summary.get("passed", 0))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
