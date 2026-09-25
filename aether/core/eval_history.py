"""Nightly VM evaluation history (``<data dir>/eval/``).

``history.jsonl`` holds one short line per night: when, which commit, how
many tasks passed, and which failed. ``runs/<stamp>.json`` keeps each full
summary. The dashboard draws the pass rate over time from the history.
"""
from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Any

from .paths import data_dir

DEFAULT_BAR_PCT = 60.0


def eval_dir() -> Path:
    return data_dir() / "eval"


def history_path() -> Path:
    return eval_dir() / "history.jsonl"


def record(status: str, *, summary: dict[str, Any] | None = None, started_at: float = 0.0,
           finished_at: float | None = None, commit: str = "", reason: str = "") -> dict:
    """Append one night's result. ``status`` is ok, skipped or error."""
    finished_at = time.time() if finished_at is None else finished_at
    summary = summary or {}
    results = summary.get("results") or []
    entry: dict[str, Any] = {
        "at": finished_at,
        "date": time.strftime("%Y-%m-%d", time.localtime(finished_at)),
        "status": status,
        "commit": commit,
        "minutes": round((finished_at - started_at) / 60.0, 1) if started_at else None,
    }
    if reason:
        entry["reason"] = reason[:300]
    if status == "ok":
        entry.update({
            "total": int(summary.get("total", 0)),
            "passed": int(summary.get("passed", 0)),
            "pass_rate_pct": float(summary.get("pass_rate_pct", 0.0)),
            "bar_pct": float(summary.get("bar_pct", DEFAULT_BAR_PCT)),
            "meets_bar": bool(summary.get("meets_bar", False)),
            "failed": [r.get("id") for r in results if not r.get("passed")][:50],
            "skipped": list(summary.get("skipped") or [])[:50],
            "recipes_passed": list(summary.get("recipes_passed") or []),
        })
    d = eval_dir()
    (d / "runs").mkdir(parents=True, exist_ok=True)
    if summary:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(finished_at))
        (d / "runs" / f"{stamp}.json").write_text(json.dumps(summary, indent=2))
    with open(history_path(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return entry


def load(limit: int = 60) -> list[dict[str, Any]]:
    """The most recent ``limit`` entries, oldest first."""
    try:
        lines = history_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out[-max(1, limit):]


def trend(entries: list[dict[str, Any]]) -> dict[str, Any]:
    ran = [e for e in entries if e.get("status") == "ok"]
    if not ran:
        return {"runs": 0}
    rates = [float(e.get("pass_rate_pct", 0.0)) for e in ran]
    streak = 0
    for e in reversed(ran):
        if not e.get("meets_bar"):
            break
        streak += 1
    last7 = rates[-7:]
    return {"runs": len(ran), "last": ran[-1], "best_pct": max(rates),
            "avg_last_7_pct": round(sum(last7) / len(last7), 1),
            "nights_meeting_bar_in_a_row": streak}


def sparkline_svg(entries: list[dict[str, Any]], *, width: int = 640, height: int = 90) -> str:
    """Pass rate per night as a small SVG line, with the bar as a dashed line."""
    ran = [e for e in entries if e.get("status") == "ok"]
    pad = 6
    bar = float(ran[-1].get("bar_pct", DEFAULT_BAR_PCT)) if ran else DEFAULT_BAR_PCT

    def y(pct: float) -> float:
        return round(height - pad - (height - 2 * pad) * max(0.0, min(pct, 100.0)) / 100.0, 1)

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}" role="img" aria-label="Pass rate by night">',
             f'<line x1="{pad}" x2="{width - pad}" y1="{y(bar)}" y2="{y(bar)}" '
             'stroke="#8b949e" stroke-dasharray="4 4"/>']
    if ran:
        step = (width - 2 * pad) / max(1, len(ran) - 1)
        pts = [(round(pad + i * step, 1), y(float(e.get("pass_rate_pct", 0.0))))
               for i, e in enumerate(ran)]
        parts.append('<polyline fill="none" stroke="#3fb950" stroke-width="2" points="'
                     + " ".join(f"{x},{yy}" for x, yy in pts) + '"/>')
        for (x, yy), e in zip(pts, ran):
            color = "#3fb950" if e.get("meets_bar") else "#f85149"
            label = html.escape(f"{e.get('date', '')}: {e.get('pass_rate_pct', 0)}%")
            parts.append(f'<circle cx="{x}" cy="{yy}" r="3" fill="{color}">'
                         f"<title>{label}</title></circle>")
    parts.append("</svg>")
    return "".join(parts)
