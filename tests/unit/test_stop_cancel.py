"""STOP cancellation and LLM abort tests (Phase 12, FR-26)."""
from __future__ import annotations

import threading
import time

import pytest

from aether.core import stop as stop_ctl
from aether.core.llm import LLM, _check_abort


class TestStopAbort:
    def setup_method(self) -> None:
        stop_ctl.reset()

    def test_trigger_sets_abort_event(self) -> None:
        assert not stop_ctl.abort_event().is_set()
        stop_ctl.trigger("test")
        assert stop_ctl.is_set()
        assert stop_ctl.abort_event().is_set()

    def test_reset_clears_abort(self) -> None:
        stop_ctl.trigger("test")
        stop_ctl.reset()
        assert not stop_ctl.is_set()

    def test_check_raises_when_stopped(self) -> None:
        stop_ctl.trigger("test")
        with pytest.raises(stop_ctl.StopRequested):
            stop_ctl.check()

    def test_check_abort_raises(self) -> None:
        stop_ctl.trigger("test")
        with pytest.raises(stop_ctl.StopRequested):
            _check_abort(stop_ctl.abort_event())

    def test_http_closer_called_on_trigger(self) -> None:
        called = threading.Event()

        def closer() -> None:
            called.set()

        stop_ctl.register_http_closer(closer)
        stop_ctl.trigger("test")
        assert called.wait(timeout=1.0)

    def test_llm_step_aborts_before_call(self) -> None:
        stop_ctl.trigger("test")
        llm = LLM(api_key="test-key", model="claude-test")
        with pytest.raises(stop_ctl.StopRequested):
            llm.step("sys", [{"role": "user", "content": "hi"}], [], abort_event=stop_ctl.abort_event())

    def test_stop_latency_under_budget_in_process(self) -> None:
        stop_ctl.reset()
        start = time.perf_counter()
        stop_ctl.trigger("latency-test")
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 200.0


@pytest.mark.integration
class TestSidecarStopMetrics:
    def test_stop_returns_latency(self, sidecar_client) -> None:
        resp = sidecar_client.post("/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "stopped"
        assert "stop_latency_ms" in data
        assert data["stop_latency_ms"] < 200.0

    def test_metrics_stop_endpoint(self, sidecar_client) -> None:
        resp = sidecar_client.post(
            "/metrics/stop",
            json={"stop_latency_ms": 42.5, "source": "test"},
        )
        assert resp.status_code == 200
        metrics = sidecar_client.get("/metrics").json()
        hist = metrics.get("histograms", {}).get("stop_latency_ms", {})
        assert hist.get("count", 0) >= 1
