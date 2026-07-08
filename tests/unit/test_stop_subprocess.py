"""STOP interrupts an in-flight delegated subprocess within ~1s (Phase 9)."""
from __future__ import annotations

import threading
import time

import pytest

from aether.core import stop as stop_ctl
from aether.tools import delegation


@pytest.fixture(autouse=True)
def _reset_stop():
    stop_ctl.reset()
    yield
    stop_ctl.reset()


def test_stop_kills_delegated_subprocess_fast(tmp_path, monkeypatch):
    # register a fake "coder" that is really `sleep 30`
    monkeypatch.setitem(delegation.AGENT_COMMANDS, "sleeper", ["sleep"])

    result_box = {}

    def run():
        result_box["r"] = delegation.delegate_to_coder_structured(
            prompt="30", agent="sleeper", workspace=str(tmp_path),
            timeout_sec=30, approved_roots=[str(tmp_path)],
        )

    t = threading.Thread(target=run)
    started = time.monotonic()
    t.start()
    time.sleep(0.4)                 # let the subprocess spawn
    stop_ctl.trigger("test")        # fire STOP
    t.join(timeout=5)
    elapsed = time.monotonic() - started

    assert not t.is_alive(), "delegation did not return after STOP"
    assert elapsed < 3.0, f"STOP took too long: {elapsed:.1f}s"
    # The process was killed (a signal / stop code), not a clean sleep completion.
    # Either the closer path (SIGTERM → exit -15) or the poll path (130) is valid.
    r = result_box["r"]
    assert r.exit_code != 0
    assert r.exit_code in (130, 143, -15, -9) or "stop" in r.summary.lower()
