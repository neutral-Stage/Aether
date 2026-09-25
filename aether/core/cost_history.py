"""What past tasks cost, so the app can say what the next one will likely cost.

One line per finished agent run in ``<data dir>/run_costs.jsonl``: when, the
estimated model cost, steps, and how it ended. No goals or content are kept.
"""
from __future__ import annotations

import json
import statistics
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .paths import data_dir

if TYPE_CHECKING:
    from .metrics import RunMetrics

# Override hook (tests); None → <data dir>/run_costs.jsonl.
PATH: Path | None = None
KEEP = 500          # lines kept when the file is compacted
RECENT = 30         # runs the estimate looks at
_lock = threading.Lock()


def _path() -> Path:
    return PATH if PATH is not None else data_dir() / "run_costs.jsonl"


def record(run: RunMetrics) -> None:
    """MetricsCollector.on_run_end hook. Runs with no steps are not kept."""
    if run.steps <= 0:
        return
    line = json.dumps({"ts": round(run.finished_at or time.time(), 3),
                       "cost_usd": round(run.cost_usd, 6), "steps": run.steps,
                       "status": run.status})
    path = _path()
    with _lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            _compact(path)
        except OSError:
            pass


def _compact(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) > KEEP * 2:
        path.write_text("\n".join(lines[-KEEP:]) + "\n", encoding="utf-8")


def _recent() -> list[dict[str, Any]]:
    path = _path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-RECENT:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and isinstance(row.get("cost_usd"), (int, float)):
            out.append(row)
    return out


def estimate(cap_usd: float) -> dict[str, Any]:
    """Typical and high (90th percentile) cost of recent tasks, and the per-task cap."""
    rows = _recent()
    costs = sorted(float(r["cost_usd"]) for r in rows)
    out: dict[str, Any] = {"runs": len(costs), "cap_usd": cap_usd,
                           "typical_usd": None, "high_usd": None, "typical_steps": None}
    if costs:
        out["typical_usd"] = round(statistics.median(costs), 4)
        out["high_usd"] = round(costs[min(len(costs) - 1, int(0.9 * len(costs)))], 4)
        out["typical_steps"] = int(statistics.median(int(r.get("steps") or 0) for r in rows))
    return out


def summary(est: dict[str, Any]) -> str:
    """One line for the app, e.g. "Tasks usually cost about $0.02 · stops at $2.00"."""
    cap = float(est.get("cap_usd") or 0)
    stop = f"stops at ${cap:.2f}" if cap > 0 else "no cost limit"
    typical = est.get("typical_usd")
    if typical is None:
        return f"No cost history yet · {stop}"
    if typical < 0.005:
        usual = "under $0.01"
    else:
        usual = f"about ${typical:.2f}"
    return f"Tasks usually cost {usual} · {stop}"
