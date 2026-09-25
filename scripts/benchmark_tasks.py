#!/usr/bin/env python3
"""Automated task benchmark runner (Phase 6).

Mock mode (default, CI-safe):
  python scripts/benchmark_tasks.py --mock

Live sidecar mode (requires running sidecar + API keys):
  python scripts/benchmark_tasks.py --sidecar http://127.0.0.1:8765 --token $AETHER_SIDECAR_TOKEN

Live VM mode (tests/benchmark/live_tasks.yaml in throwaway Lume clones,
scored by real end state; see docs/BENCHMARK_VM.md):
  python scripts/benchmark_tasks.py --vm [--only id1,id2] [--have mail_account]
      [--reuse-vm] [--keep] [--vm-config scripts/vm/lume.yaml] [--out results.json]
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
    parser.add_argument("--vm", action="store_true",
                        help="Run tests/benchmark/live_tasks.yaml in Lume VM clones")
    parser.add_argument("--vm-config", type=Path, default=None, help="scripts/vm/lume.yaml")
    parser.add_argument("--only", type=str, default="", help="Comma-separated task ids")
    parser.add_argument("--have", type=str, default="",
                        help="Comma-separated golden-image capabilities, e.g. mail_account")
    parser.add_argument("--reuse-vm", action="store_true",
                        help="Run all tasks in one clone (faster, less isolated)")
    parser.add_argument("--keep", action="store_true", help="Keep clones for debugging")
    parser.add_argument("--out", type=Path, default=None, help="Write the VM summary JSON here")
    parser.add_argument("--stamp-recipes", action="store_true",
                        help="Mark pack recipes whose linked task passed as tested today "
                             "(aether/knowledge/verified.json)")
    args = parser.parse_args()

    if args.vm:
        from tests.benchmark import vm_runner

        split = lambda text: [x.strip() for x in text.split(",") if x.strip()]  # noqa: E731
        tasks, skipped = vm_runner.select_tasks(
            vm_runner.load_live_tasks(), split(args.only), split(args.have))
        config = vm_runner.load_vm_config(args.vm_config)
        results = vm_runner.run_suite(tasks, config, reuse_vm=args.reuse_vm, keep=args.keep)
        summary = vm_runner.summarize_live(results, skipped)
        summary["recipes_passed"] = vm_runner.passed_recipes(tasks, results)
        if args.stamp_recipes and summary["recipes_passed"]:
            import datetime

            from aether.knowledge import loader

            loader.stamp_verified(summary["recipes_passed"], datetime.date.today().isoformat())
            print(f"Stamped {len(summary['recipes_passed'])} recipe(s) as tested: "
                  + ", ".join(summary["recipes_passed"]))
        if args.out:
            args.out.write_text(json.dumps(summary, indent=2))
        print(f"Benchmark (vm): {summary['passed']}/{summary['total']} passed "
              f"({summary['pass_rate_pct']}%, bar {summary['bar_pct']}%)"
              + (f"; skipped {', '.join(summary['skipped'])}" if summary["skipped"] else ""))
        return 0 if summary["meets_bar"] else 1

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
