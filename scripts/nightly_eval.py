#!/usr/bin/env python3
"""Nightly VM evaluation: run the live benchmark in fresh Lume clones and log the result.

Started by launchd on the host Mac (scripts/vm/install_nightly.sh), never
inside a VM. Each night it:

1. Skips when another evaluation is running, or when the Mac is on battery
   (unless --allow-battery).
2. Runs tests/benchmark/live_tasks.yaml exactly like
   ``benchmark_tasks.py --vm`` (golden image → clone per task → real end
   state checks).
3. With --stamp-recipes, marks pack recipes whose linked task passed as
   tested today.
4. Appends a line to <data dir>/eval/history.jsonl, which the sidecar's
   /dashboard and GET /eval/history show as pass rate over time.

    python scripts/nightly_eval.py [--only id1,id2] [--have mail_account]
        [--vm-config scripts/vm/lume.yaml] [--stamp-recipes] [--allow-battery]
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.core import eval_history  # noqa: E402


def on_battery() -> bool:
    """True only when pmset says so; unknown counts as plugged in."""
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True,  # noqa: S603, S607
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "Battery Power" in out.splitlines()[0] if out else False


def git_commit() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,  # noqa: S603, S607
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


@contextlib.contextmanager
def single_run() -> Iterator[bool]:
    """Yields False when another evaluation holds the lock."""
    d = eval_history.eval_dir()
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "nightly.lock", "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def run(args: argparse.Namespace) -> int:
    from tests.benchmark import vm_runner

    started = time.time()
    commit = git_commit()
    split = lambda text: [x.strip() for x in (text or "").split(",") if x.strip()]  # noqa: E731
    try:
        tasks, skipped = vm_runner.select_tasks(vm_runner.load_live_tasks(), split(args.only),
                                                split(args.have))
        config = vm_runner.load_vm_config(args.vm_config)
        results = vm_runner.run_suite(tasks, config, reuse_vm=False, keep=False)
        summary = vm_runner.summarize_live(results, skipped)
        summary["recipes_passed"] = vm_runner.passed_recipes(tasks, results)
    except Exception as e:  # noqa: BLE001 — a broken night is recorded, not lost
        eval_history.record("error", started_at=started, commit=commit,
                            reason=f"{type(e).__name__}: {e}")
        print(f"Nightly eval failed: {e}", file=sys.stderr)
        return 1
    if args.stamp_recipes and summary["recipes_passed"]:
        from aether.knowledge import loader

        loader.stamp_verified(summary["recipes_passed"], time.strftime("%Y-%m-%d"))
    entry = eval_history.record("ok", summary=summary, started_at=started, commit=commit)
    print(f"Nightly eval {entry['date']} ({commit or 'unknown commit'}): "
          f"{entry['passed']}/{entry['total']} passed ({entry['pass_rate_pct']}%, "
          f"bar {entry['bar_pct']}%)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vm-config", type=Path, default=None)
    parser.add_argument("--only", default="", help="Comma-separated task ids")
    parser.add_argument("--have", default="", help="Comma-separated capabilities of the image")
    parser.add_argument("--stamp-recipes", action="store_true")
    parser.add_argument("--allow-battery", action="store_true")
    args = parser.parse_args(argv)
    with single_run() as mine:
        if not mine:
            print("Another evaluation is running; skipping.")
            return 0
        if not args.allow_battery and on_battery():
            eval_history.record("skipped", reason="on battery power")
            print("On battery power; skipping tonight's evaluation.")
            return 0
        return run(args)


if __name__ == "__main__":
    sys.exit(main())
