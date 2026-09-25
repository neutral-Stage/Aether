"""Warm pool: a spare Claude Code process started ahead of time (yoclicky's idea).

Claude Code in stream-json mode reads the task from stdin, so the slow part
(starting the CLI, loading its config and MCP servers) can happen before the
task exists. After a Claude session starts in a folder, the manager starts
one spare for the same folder and settings. The next session there adopts
it and starts instantly.

A spare is only used when it matches exactly: the same folder, isolation,
permission mode, allowed tools, MCP setting and environment allowlist. An
isolated spare also has its worktree ready, made from the repo's HEAD at
the time; if HEAD has moved since, the spare is thrown away. Spares expire
after ``max_age_sec``, at most ``max_total`` exist, and STOP drains them.
A discarded spare's worktree and its unused branch are removed.

Spares are not started at a lower priority: macOS cannot raise a process's
priority back without root, and an adopted session must run at full speed.
An idle spare uses no CPU.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import worktree as wt

log = logging.getLogger(__name__)


@dataclass
class Warm:
    key: tuple
    session_id: str
    run_dir: str
    branch: str | None
    head: str                   # commit the worktree was made from ("" = not isolated)
    proc: Any                   # subprocess.Popen
    workspace: str              # the folder the session asked for (repo, not worktree)
    mcp_config: str | None = None
    warning: str | None = None
    created_at: float = 0.0

    def alive(self) -> bool:
        return self.proc.poll() is None


class WarmPool:
    def __init__(self, *, max_total: int = 2, max_age_sec: float = 900.0,
                 clock: Callable[[], float] = time.time) -> None:
        self.max_total = max(0, int(max_total))
        self.max_age_sec = float(max_age_sec)
        self.clock = clock
        self._lock = threading.Lock()
        self._items: dict[tuple, Warm] = {}
        self._filling: set[tuple] = set()
        self._threads: list[threading.Thread] = []
        self.adopted = 0

    def take(self, key: tuple, head: str = "") -> Warm | None:
        """The spare for ``key`` if it is alive, fresh and made from ``head``."""
        with self._lock:
            warm = self._items.pop(key, None)
        if warm is None:
            return None
        stale = (not warm.alive() or self.clock() - warm.created_at > self.max_age_sec
                 or (warm.branch and warm.head != head))
        if stale:
            self._discard(warm)
            return None
        with self._lock:
            self.adopted += 1
        return warm

    def put(self, warm: Warm) -> None:
        evicted: list[Warm] = []
        with self._lock:
            old = self._items.pop(warm.key, None)
            if old is not None:
                evicted.append(old)
            while self._items and len(self._items) >= self.max_total:
                oldest = min(self._items.values(), key=lambda w: w.created_at)
                evicted.append(self._items.pop(oldest.key))
            if self.max_total > 0:
                self._items[warm.key] = warm
            else:
                evicted.append(warm)
        for w in evicted:
            self._discard(w)

    def fill_async(self, key: tuple, factory: Callable[[], Warm | None]) -> bool:
        """Start a spare for ``key`` in the background (at most one fill per key)."""
        if self.max_total <= 0:
            return False
        with self._lock:
            if key in self._filling or key in self._items:
                return False
            self._filling.add(key)

        def fill() -> None:
            try:
                warm = factory()
                if warm is not None:
                    warm.created_at = warm.created_at or self.clock()
                    self.put(warm)
            except Exception as e:  # noqa: BLE001 — a failed spare only costs latency
                log.info("warm pool: could not start a spare: %s", e)
            finally:
                with self._lock:
                    self._filling.discard(key)

        t = threading.Thread(target=fill, daemon=True, name="fleet-warm-fill")
        with self._lock:
            self._threads = [x for x in self._threads if x.is_alive()] + [t]
        t.start()
        return True

    def wait_idle(self, timeout: float = 10.0) -> None:
        """Test hook: wait for background fills to finish."""
        with self._lock:
            threads = list(self._threads)
        for t in threads:
            t.join(timeout)

    def expire(self, now: float | None = None) -> int:
        now = self.clock() if now is None else now
        with self._lock:
            gone = [w for w in self._items.values()
                    if not w.alive() or now - w.created_at > self.max_age_sec]
            for w in gone:
                self._items.pop(w.key, None)
        for w in gone:
            self._discard(w)
        return len(gone)

    def drain(self) -> int:
        with self._lock:
            items = list(self._items.values())
            self._items.clear()
        for w in items:
            self._discard(w)
        return len(items)

    def status(self) -> list[dict[str, Any]]:
        now = self.clock()
        with self._lock:
            return [{"workspace": w.workspace, "session_id": w.session_id,
                     "isolated": bool(w.branch), "age_sec": round(now - w.created_at, 1),
                     "alive": w.alive()} for w in self._items.values()]

    @staticmethod
    def _discard(warm: Warm) -> None:
        proc = warm.proc
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except (OSError, ProcessLookupError):
            pass
        if warm.mcp_config:
            Path(warm.mcp_config).unlink(missing_ok=True)
        if warm.branch:
            wt.discard(Path(warm.workspace), warm.run_dir, warm.branch)
