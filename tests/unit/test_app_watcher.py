"""AppWatcher: watch/unwatch, diffing, event emission — mocked AX."""
from __future__ import annotations

import aether.perception.app_watcher as aw
from aether.perception.app_watcher import AppWatcher, AppSnapshot, _diff


def test_diff_detects_new_content():
    old = AppSnapshot(app="Mail", pid=1, window_titles=["Inbox"], text_lines=["a", "b"])
    new = AppSnapshot(app="Mail", pid=1, window_titles=["Inbox"], text_lines=["a", "b", "c"])
    events = _diff(old, new)
    assert any(e["kind"] == "content_change" and "c" in e["detail"] for e in events)


def test_diff_detects_window_change():
    old = AppSnapshot(app="Xcode", pid=2, window_titles=["main.swift"])
    new = AppSnapshot(app="Xcode", pid=2, window_titles=["main.swift", "Build Succeeded"])
    events = _diff(old, new)
    assert any(e["kind"] == "window_change" for e in events)


def test_diff_stable_no_events():
    snap = AppSnapshot(app="Notes", pid=3, window_titles=["N"], text_lines=["x"])
    assert _diff(snap, snap) == []


def test_watch_unwatch_lifecycle(monkeypatch):
    AppWatcher.reset()
    monkeypatch.setattr(aw.ax, "resolve_app",
                        lambda name: {"name": "Mail", "pid": 42, "bundle": "com.apple.mail",
                                      "active": False})
    monkeypatch.setattr(aw, "_take_snapshot",
                        lambda name: AppSnapshot(app="Mail", pid=42))
    w = AppWatcher.get()
    w._stop.set()  # don't spin the poll thread in tests
    assert "Watching Mail" in w.watch("Mail")
    assert "Mail" in w.watched()
    assert "Already watching" in w.watch("mail")  # idempotent, case-insensitive
    assert "Stopped watching" in w.unwatch("Mail")
    assert w.watched() == []
    AppWatcher.reset()


def test_watch_rejects_unknown_app(monkeypatch):
    AppWatcher.reset()
    monkeypatch.setattr(aw.ax, "resolve_app", lambda name: None)
    assert "not running" in AppWatcher.get().watch("Nope")
    AppWatcher.reset()


def test_poll_emits_event_to_sink(monkeypatch):
    AppWatcher.reset()
    monkeypatch.setattr(aw.ax, "resolve_app",
                        lambda name: {"name": "Xcode", "pid": 9, "bundle": "x", "active": False})
    snaps = iter([
        AppSnapshot(app="Xcode", pid=9, window_titles=["a"]),          # initial
        AppSnapshot(app="Xcode", pid=9, window_titles=["a", "Done"]),  # poll
    ])
    monkeypatch.setattr(aw, "_take_snapshot", lambda name: next(snaps))
    w = AppWatcher.get()
    w._stop.set()
    got: list[dict] = []
    w.set_event_sink(got.append)
    w.watch("Xcode")     # consumes initial snapshot
    w._poll_one("Xcode")  # consumes second snapshot, diffs
    assert any(e["kind"] == "window_change" and e["app"] == "Xcode" for e in got)
    AppWatcher.reset()


def test_poll_app_quit_emits_without_deadlock(monkeypatch):
    """App-quit branch must emit OUTSIDE the lock — else the poll thread hangs."""
    AppWatcher.reset()
    monkeypatch.setattr(aw.ax, "resolve_app",
                        lambda name: {"name": "Gone", "pid": 5, "bundle": "g", "active": False})
    calls = {"n": 0}

    def snap(name):
        calls["n"] += 1
        return AppSnapshot(app="Gone", pid=5) if calls["n"] == 1 else None

    monkeypatch.setattr(aw, "_take_snapshot", snap)
    w = AppWatcher.get()
    w._stop.set()
    got: list[dict] = []
    w.set_event_sink(got.append)
    w.watch("Gone")       # snapshot #1 (present)
    w._poll_one("Gone")   # snapshot #2 = None → app_quit; must not deadlock on _lock
    assert any(e["kind"] == "app_quit" for e in got)
    assert "Gone" not in w.watched()
    AppWatcher.reset()


def test_summary_lines_budget(monkeypatch):
    AppWatcher.reset()
    w = AppWatcher.get()
    for i in range(3):
        w._snapshots[f"App{i}"] = AppSnapshot(app=f"App{i}", pid=i, element_count=i)
    lines = w.summary_lines(budget=2)
    assert len(lines) == 2
    AppWatcher.reset()
