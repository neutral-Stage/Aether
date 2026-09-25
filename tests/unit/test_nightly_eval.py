"""Nightly VM evaluation: history, the launchd script, and the dashboard."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from aether.core import eval_history
from aether.core.metrics import MetricsCollector
from tests.benchmark import vm_runner

ROOT = Path(__file__).resolve().parents[2]


def _load_script():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("nightly_eval", ROOT / "scripts" / "nightly_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setenv("AETHER_DATA_DIR", str(tmp_path))
    return tmp_path


def _summary(passed: int, total: int = 4) -> dict:
    results = [vm_runner.LiveResult(f"t{i}", i < passed, "") for i in range(total)]
    s = vm_runner.summarize_live(results)
    s["recipes_passed"] = ["notes.create_note"]
    return s


def test_record_load_and_trend(data) -> None:  # noqa: ANN001
    for n, when in ((1, 1_000), (3, 90_000), (4, 180_000)):
        eval_history.record("ok", summary=_summary(n), started_at=when - 600,
                            finished_at=when, commit="abc123")
    eval_history.record("skipped", reason="on battery power", finished_at=200_000)
    entries = eval_history.load()
    assert [e["status"] for e in entries] == ["ok", "ok", "ok", "skipped"]
    assert entries[0]["failed"] == ["t1", "t2", "t3"] and entries[0]["minutes"] == 10.0
    assert entries[2]["meets_bar"] and entries[2]["recipes_passed"] == ["notes.create_note"]
    t = eval_history.trend(entries)
    assert t["runs"] == 3 and t["best_pct"] == 100.0 and t["nights_meeting_bar_in_a_row"] == 2
    assert t["avg_last_7_pct"] == round((25 + 75 + 100) / 3, 1)
    assert len(list((data / "eval" / "runs").iterdir())) == 3
    (data / "eval" / "history.jsonl").open("a").write("not json\n")
    assert len(eval_history.load()) == 4 and len(eval_history.load(limit=2)) == 2
    assert eval_history.trend([]) == {"runs": 0}


def test_sparkline() -> None:
    svg = eval_history.sparkline_svg([
        {"status": "ok", "pass_rate_pct": 50.0, "meets_bar": False, "date": "<b>", "bar_pct": 60},
        {"status": "ok", "pass_rate_pct": 70.0, "meets_bar": True, "date": "d2", "bar_pct": 60}])
    assert svg.startswith("<svg") and "<polyline" in svg and "stroke-dasharray" in svg
    assert "&lt;b&gt;" in svg and "<b>" not in svg
    assert "#f85149" in svg and "#3fb950" in svg
    assert "<polyline" not in eval_history.sparkline_svg([])


def test_nightly_script_records_runs_errors_and_skips(monkeypatch) -> None:  # noqa: ANN001
    mod = _load_script()
    monkeypatch.setattr(mod, "on_battery", lambda: False)
    monkeypatch.setattr(mod, "git_commit", lambda: "f00d")
    seen = {}

    def fake_suite(tasks, config, *, reuse_vm, keep):  # noqa: ANN001, ANN202
        seen["reuse"], seen["ids"] = reuse_vm, [t["id"] for t in tasks]
        return [vm_runner.LiveResult(t["id"], True, "ok") for t in tasks]

    monkeypatch.setattr(vm_runner, "run_suite", fake_suite)
    stamped = []
    from aether.knowledge import loader

    monkeypatch.setattr(loader, "stamp_verified", lambda names, day: stamped.append(names))
    task_id = vm_runner.load_live_tasks()[0]["id"]
    assert mod.main(["--only", task_id, "--stamp-recipes"]) == 0
    entry = eval_history.load()[-1]
    assert entry["status"] == "ok" and entry["commit"] == "f00d" and entry["passed"] == 1
    assert seen == {"reuse": False, "ids": [task_id]}

    monkeypatch.setattr(vm_runner, "run_suite", lambda *a, **k: 1 / 0)
    assert mod.main([]) == 1
    assert eval_history.load()[-1]["status"] == "error"
    assert "ZeroDivisionError" in eval_history.load()[-1]["reason"]

    monkeypatch.setattr(mod, "on_battery", lambda: True)
    assert mod.main([]) == 0 and eval_history.load()[-1]["reason"] == "on battery power"


def test_only_one_evaluation_at_a_time() -> None:
    mod = _load_script()
    with mod.single_run() as first:
        assert first
        with mod.single_run() as second:
            assert not second
    with mod.single_run() as again:
        assert again


def test_dashboard_and_history_endpoint(sidecar_client) -> None:  # noqa: ANN001
    empty = sidecar_client.get("/dashboard").text
    assert "No runs yet" in empty
    eval_history.record("ok", summary=_summary(3), commit="abc")
    metrics = MetricsCollector.get()
    metrics.start_run("r-xss", "<script>alert(1)</script>")
    metrics.end_run("done")
    page = sidecar_client.get("/dashboard").text
    assert "Nightly evaluation" in page and "<svg" in page and "3/4" in page
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    data = sidecar_client.get("/eval/history").json()
    assert data["trend"]["runs"] == 1 and data["entries"][0]["commit"] == "abc"
    assert json.dumps(data)
