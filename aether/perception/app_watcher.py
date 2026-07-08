"""Background app watcher — surfaces changes in non-frontmost apps (Phase 4).

Polls each watched app's AX tree on a background thread and diffs snapshots
into events (title change, content change, window count). Works for any app
AX can read, including Notification Center banners (watch "Notification Center").

# ponytail: polling (default 2s) — per-app AXObserver on a CFRunLoop thread is
# the upgrade path if sub-second event latency ever matters; pyobjc observer
# callbacks are fragile across versions, polling is not.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from . import accessibility as ax

log = logging.getLogger(__name__)

MAX_WATCHED = 6
POLL_INTERVAL_SEC = 2.0
EVENT_BUFFER = 50
TRIGGER_COOLDOWN_SEC = 30.0  # min gap between refires of the same trigger rule


@dataclass
class AppSnapshot:
    app: str
    pid: int
    window_titles: list[str] = field(default_factory=list)
    element_count: int = 0
    text_lines: list[str] = field(default_factory=list)  # top static text
    rendered: str = ""
    ts: float = field(default_factory=time.time)


def _take_snapshot(app_name: str) -> AppSnapshot | None:
    info = ax.resolve_app(app_name)
    if info is None:
        return None
    els = ax.read_tree(max_elements=40, capture_handles=False, pid=info["pid"])
    texts = [e.value or e.title for e in els
             if e.role in ("AXStaticText", "AXTextArea") and (e.value or e.title)]
    titles = []
    for w in ax.list_windows():
        if w["pid"] == info["pid"]:
            titles = w["windows"]
            break
    return AppSnapshot(
        app=info["name"], pid=info["pid"], window_titles=titles,
        element_count=len(els), text_lines=texts[:10],
        rendered="\n".join(e.describe() for e in els[:8]),
    )


def _diff(old: AppSnapshot, new: AppSnapshot) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if old.window_titles != new.window_titles:
        added = [t for t in new.window_titles if t not in old.window_titles]
        events.append({"kind": "window_change",
                       "detail": f"windows now: {new.window_titles[:3]}"
                                 + (f" (new: {added[:2]})" if added else "")})
    fresh = [t for t in new.text_lines if t not in old.text_lines]
    if fresh:
        events.append({"kind": "content_change", "detail": "; ".join(fresh[:3])[:200]})
    return events


class AppWatcher:
    """Singleton polling watcher for background apps."""

    _instance: AppWatcher | None = None
    _cls_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshots: dict[str, AppSnapshot] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=EVENT_BUFFER)
        self._sink: Callable[[dict[str, Any]], None] | None = None
        self._triggers: dict[str, list[dict[str, Any]]] = {}  # app → trigger rules
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.poll_interval = POLL_INTERVAL_SEC

    @classmethod
    def get(cls) -> AppWatcher:
        with cls._cls_lock:
            if cls._instance is None:
                cls._instance = AppWatcher()
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._cls_lock:
            if cls._instance is not None:
                cls._instance.stop()
            cls._instance = None

    def set_event_sink(self, fn: Callable[[dict[str, Any]], None] | None) -> None:
        self._sink = fn

    def watch(self, app_name: str) -> str:
        info = ax.resolve_app(app_name)
        if info is None:
            return f"ERROR: app not running: {app_name}"
        with self._lock:
            if info["name"] in self._snapshots:
                return f"Already watching {info['name']}."
            if len(self._snapshots) >= MAX_WATCHED:
                return f"ERROR: already watching {MAX_WATCHED} apps — unwatch one first."
            snap = _take_snapshot(info["name"])
            self._snapshots[info["name"]] = snap or AppSnapshot(
                app=info["name"], pid=info["pid"])
        self._ensure_thread()
        return f"Watching {info['name']} (pid {info['pid']})."

    def unwatch(self, app_name: str) -> str:
        with self._lock:
            self._triggers.pop(app_name, None)
            for key in list(self._snapshots):
                if key.lower() == app_name.strip().lower():
                    del self._snapshots[key]
                    self._triggers.pop(key, None)
                    return f"Stopped watching {key}."
        return f"Not watching {app_name}."

    def add_trigger(
        self, app_name: str, contains: str = "", goal: str = "", auto: bool = False,
    ) -> str:
        """Fire `goal` (as a suggestion, or auto-run when configured) when a
        watched app changes and the change text contains `contains` (Phase 10)."""
        info = ax.resolve_app(app_name)
        name = info["name"] if info else app_name
        # A trigger implies watching the app — if that fails (app not running,
        # too many watched), the trigger can never fire, so report the error.
        if name not in self.watched():
            watch_result = self.watch(name)
            if watch_result.startswith("ERROR"):
                return watch_result
        with self._lock:
            self._triggers.setdefault(name, []).append(
                {"contains": contains, "goal": goal, "auto": bool(auto), "last_fired": 0.0})
        cond = f"'{contains}'" if contains else "any change"
        return f"Trigger on {name}: when {cond} → {goal!r} (auto={auto})."

    def watched(self) -> list[str]:
        with self._lock:
            return list(self._snapshots)

    def recent_events(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)[-limit:]

    def summary_lines(self, budget: int = 5) -> list[str]:
        """Compact per-app lines for world-model context injection."""
        with self._lock:
            snaps = list(self._snapshots.values())[:budget]
            recent = {e["app"]: e for e in self._events}
        lines = []
        for s in snaps:
            last = recent.get(s.app)
            evt = f" | last: {last['kind']}: {last['detail'][:80]}" if last else ""
            title = s.window_titles[0] if s.window_titles else ""
            lines.append(f"- {s.app} \"{title[:50]}\" ({s.element_count} els){evt}")
        return lines

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=self.poll_interval + 1)
        self._thread = None

    # -- internals --

    def _ensure_thread(self) -> None:
        # Locked check-create-assign: concurrent watch() calls (sidecar dispatches
        # each via asyncio.to_thread) must not both spawn a poll thread.
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="aether-app-watcher", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_interval):
            with self._lock:
                names = list(self._snapshots)
            if not names:
                return  # nothing watched — let the thread die; watch() restarts it
            for name in names:
                try:
                    self._poll_one(name)
                except Exception as e:  # noqa: BLE001
                    log.debug("watcher poll failed for %s: %s", name, e)

    def _poll_one(self, name: str) -> None:
        new = _take_snapshot(name)
        quit_event: dict[str, Any] | None = None
        events: list[dict[str, Any]] = []
        with self._lock:
            old = self._snapshots.get(name)
            if old is None:
                return
            if new is None:  # app quit
                del self._snapshots[name]
                quit_event = {"app": name, "kind": "app_quit", "detail": "app terminated"}
            else:
                events = _diff(old, new)
                self._snapshots[name] = new
        # Emit OUTSIDE the lock — _emit re-acquires it (non-reentrant).
        if quit_event is not None:
            self._emit(quit_event)
            return
        for e in events:
            self._emit({"app": name, **e})

    def _fire_triggers(self, event: dict[str, Any]) -> None:
        app = event.get("app", "")
        detail = str(event.get("detail", "")).lower()
        now = time.time()
        fired: list[dict[str, Any]] = []
        with self._lock:
            for rule in self._triggers.get(app, []):
                needle = (rule.get("contains") or "").lower()
                if needle and needle not in detail:
                    continue
                # Cooldown: a continuously-changing app (timers, log tails) must
                # not refire the trigger every poll — storm guard.
                if now - float(rule.get("last_fired", 0.0)) < TRIGGER_COOLDOWN_SEC:
                    continue
                rule["last_fired"] = now
                fired.append(rule)
        for rule in fired:  # emit outside the lock
            self._emit({
                "app": app, "kind": "app_trigger",
                "goal": rule.get("goal", ""), "auto": rule.get("auto", False),
                "detail": (f"trigger matched ({rule.get('contains') or 'any change'})"
                           f" on {event.get('kind')}"),
            })

    def _emit(self, event: dict[str, Any]) -> None:
        event = {**event, "ts": time.time()}
        with self._lock:
            self._events.append(event)
        # Check triggers for real changes (not for trigger events themselves).
        if event.get("kind") != "app_trigger":
            self._fire_triggers(event)
        sink = self._sink
        if sink is not None:
            try:
                sink(event)
            except Exception as e:  # noqa: BLE001
                log.debug("app_event sink failed: %s", e)
