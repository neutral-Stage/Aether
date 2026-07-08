"""Executor: HID lock + idle-guard focus juggling (mocked Quartz/AppKit)."""
from __future__ import annotations

import pytest

import aether.effectors.executor as ex


def test_focused_action_refuses_when_user_active(monkeypatch):
    monkeypatch.setattr(ex, "user_idle_seconds", lambda: 0.3)
    with pytest.raises(RuntimeError, match="user is active"):
        ex.focused_action(123, lambda: "should not run", idle_threshold=2.0)


def test_focused_action_runs_when_idle_and_restores(monkeypatch):
    monkeypatch.setattr(ex, "user_idle_seconds", lambda: 5.0)
    activated: list[int] = []
    monkeypatch.setattr(ex, "_activate", lambda pid: activated.append(pid) or True)
    monkeypatch.setattr(ex, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))

    class FakeAX:
        @staticmethod
        def frontmost_app():
            return {"name": "Prev", "pid": 999}

    import aether.perception.accessibility as real_ax
    monkeypatch.setattr(real_ax, "frontmost_app", FakeAX.frontmost_app)

    result = ex.focused_action(123, lambda: "did it", idle_threshold=2.0, settle_sec=0)
    assert result == "did it"
    assert activated == [123, 999]  # target activated, then previous restored


def test_hid_lock_is_reentrant():
    with ex.HID_LOCK:
        with ex.HID_LOCK:  # RLock — must not deadlock
            assert True
