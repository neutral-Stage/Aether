"""Screen memory recorder: remembers the text of what you look at, if you turn it on.

Every ``poll_s`` it checks what is in front. A new app or window is recorded
once it has stayed in front for ``settle_s`` (so passing through doesn't
count); a window that stays in front is re-read every ``interval_s`` and
stored only if its text changed. Text comes from the accessibility tree; when
that shows almost nothing (canvas and Electron apps), on-device OCR of that
window only is the fallback. No images are kept. Secrets are redacted before
anything is stored, the privacy gate (privacy.py, browsers.py and gate.py)
skips whatever it isn't sure about, and old captures are pruned after
``retention_days``.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .privacy import Decision, PrivacySettings, WindowState, decide
from .store import ScreenMemoryStore

log = logging.getLogger(__name__)

MAX_TEXT = 6000
MIN_AX_TEXT = 80


@dataclass
class RecorderSettings:
    enabled: bool = False
    poll_s: float = 2.0
    settle_s: float = 1.5
    interval_s: float = 30.0
    retention_days: float = 14.0
    ocr_fallback: bool = True
    privacy: PrivacySettings = field(default_factory=PrivacySettings)

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None) -> RecorderSettings:
        from .prefs import load_prefs

        s = (raw or {}).get("screen_memory") or {}
        # Browsers allowed in config.yaml, plus whatever the owner has toggled
        # on from the menu bar (persisted outside config.yaml — see prefs.py).
        configured = {str(b) for b in s.get("allow_browsers") or []}
        persisted = {str(b) for b in load_prefs().get("allow_browsers") or []}
        return cls(
            enabled=bool(s.get("enabled", False)),
            poll_s=max(0.5, float(s.get("poll_s", 2.0))),
            settle_s=max(0.0, float(s.get("settle_s", 1.5))),
            interval_s=max(5.0, float(s.get("interval_s", 30.0))),
            retention_days=max(0.5, float(s.get("retention_days", 14))),
            ocr_fallback=bool(s.get("ocr_fallback", True)),
            privacy=PrivacySettings(
                paused=bool(s.get("paused", False)),
                exclude_bundle_ids=[str(b) for b in s.get("exclude_bundle_ids") or []],
                exclude_window_globs=[str(g) for g in s.get("exclude_window_globs") or []],
                only_bundle_ids=[str(b) for b in s.get("only_bundle_ids") or []],
                allowed_browsers=sorted(configured | persisted)))


def probe_front() -> WindowState:
    """What is in front right now (None fields where it can't be read)."""
    from ..perception import accessibility as ax

    try:
        app = ax.frontmost_app()
        if not app.get("name") or int(app.get("pid", -1)) < 0:
            return WindowState(None, None, None)
        title = ax.focused_window_title(int(app["pid"]))
        focused = ax.focused_summary()
        secure = ("Secure" in str(focused.get("role", ""))
                 or "Secure" in str(focused.get("subrole", "")))
        return WindowState(app["name"], app.get("bundle") or None, title, secure,
                           int(app["pid"]), _front_window_id(int(app["pid"])))
    except Exception:  # noqa: BLE001 — unreadable counts as unknown (not recorded)
        return WindowState(None, None, None)


def _front_window_id(pid: int) -> int:
    """Number of ``pid``'s frontmost ordinary window, or -1 when it can't be read.

    Window numbers, owners and layers don't need Screen Recording permission
    (only window names do).
    """
    try:
        import Quartz

        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID) or []
        for info in windows:   # front to back
            if (int(info.get(Quartz.kCGWindowOwnerPID, -1)) == pid
                    and int(info.get(Quartz.kCGWindowLayer, -1)) == 0):
                return int(info.get(Quartz.kCGWindowNumber, -1))
    except Exception:  # noqa: BLE001
        pass
    return -1


def read_window_text(state: WindowState, *, ocr_fallback: bool = True) -> tuple[str, str]:
    """(text, source) for the front window: accessibility first, OCR of that window only."""
    from ..perception import accessibility as ax

    lines: list[str] = []
    seen: set[str] = set()
    try:
        for el in ax.read_tree(max_elements=400, capture_handles=False, pid=state.pid):
            if "Secure" in el.role or "Secure" in el.subrole:
                continue
            for text in (el.value, el.title):
                t = " ".join(str(text or "").split())
                if len(t) >= 2 and t not in seen:
                    seen.add(t)
                    lines.append(t)
    except Exception:  # noqa: BLE001
        lines = []
    text = "\n".join(lines)[:MAX_TEXT]
    if len(text) >= MIN_AX_TEXT or not ocr_fallback:
        return text, "ax"
    ocr = _ocr_front_window()
    return (ocr[:MAX_TEXT], "ocr") if len(ocr) > len(text) else (text, "ax")


def _ocr_front_window() -> str:
    from ..perception import accessibility as ax
    from ..perception import ocr, screen

    frame = ax.focused_window_frame()
    if frame is None or not ocr.available():
        return ""
    try:
        cap = screen.capture()
        x, y, w, h = frame
        crop = screen.crop_around(cap, x + w / 2, y + h / 2, half_pt=max(w, h) / 2, scale=1.0)
        return "\n".join(r.text for r in ocr.recognize_text(crop.path))
    except Exception:  # noqa: BLE001
        return ""


class ScreenMemoryRecorder:
    def __init__(self, settings: RecorderSettings, store: ScreenMemoryStore, *,
                 probe: Callable[[], WindowState] = probe_front,
                 read_text: Callable[..., tuple[str, str]] = read_window_text,
                 check: Callable[..., tuple[bool | None, str]] | None = None,
                 redact: Callable[[str], str] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        from .browsers import check_private

        self.settings = settings
        self.store = store
        self.probe = probe
        self.read_text = read_text
        self.check = check or check_private
        self.redact = redact or _default_redact
        self.clock = clock
        self.skipped: Counter[str] = Counter()
        self._front_key: tuple[str, str] | None = None
        self._front_since = 0.0
        self._last_read: dict[tuple[str, str], float] = {}
        self._last_prune = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        # A pause survives restarts: a sidecar that crashes and comes back must
        # not quietly start recording again.
        if self.pause_marker.exists():
            self.settings.privacy.paused = True

    @property
    def pause_marker(self) -> Path:
        return self.store.path.with_name(self.store.path.name + ".paused")

    @property
    def paused(self) -> bool:
        return self.settings.privacy.paused

    def pause(self) -> None:
        self.settings.privacy.paused = True
        try:
            self.pause_marker.touch()
        except OSError:
            log.warning("could not persist the screen memory pause", exc_info=True)

    def resume(self) -> None:
        try:
            self.pause_marker.unlink(missing_ok=True)
        except OSError:
            # Stay paused rather than record while the marker says otherwise.
            log.warning("could not clear the screen memory pause", exc_info=True)
            return
        self.settings.privacy.paused = False

    def tick(self) -> str:
        """One poll. Returns what happened (recorded, unchanged, settling, skipped: …)."""
        now = self.clock()
        if now - self._last_prune > 3600:
            self._last_prune = now
            self.store.prune(self.settings.retention_days, now)
        # The cheap check (no AppleScript) that decides whether we're still
        # settling on the same window; the gate below re-does the allowed part
        # of this plus the private-window check right before actually reading.
        state = self.probe()
        decision: Decision = decide(state, self.settings.privacy)
        if not decision.allowed:
            self.skipped[decision.reason] += 1
            self._front_key = None
            return f"skipped: {decision.reason}"
        key = (str(state.bundle_id), str(state.window_title))
        if key != self._front_key:
            self._front_key = key
            self._front_since = now
            return "settling"
        if now - self._front_since < self.settings.settle_s:
            return "settling"
        last = self._last_read.get(key)
        if last is not None and now - last < self.settings.interval_s:
            return "unchanged"
        self._last_read[key] = now
        from .gate import readable_front

        source = "ax"

        def _read(s: WindowState, *, ocr_fallback: bool) -> tuple[str, str]:
            nonlocal source
            text, source = self.read_text(s, ocr_fallback=ocr_fallback)
            return text, source

        gated_state, text_or_reason = readable_front(
            self.settings.privacy, ocr_fallback=self.settings.ocr_fallback,
            probe=self.probe, read_text=_read, check=self.check)
        if gated_state is None:
            self.skipped[text_or_reason] += 1
            return f"skipped: {text_or_reason}"
        text = self.redact(text_or_reason).strip()
        if not text:
            return "unchanged"
        row = self.store.add(app=str(gated_state.app), bundle_id=str(gated_state.bundle_id),
                             window=str(gated_state.window_title), text=text, source=source,
                             ts=now)
        return "recorded" if row else "unchanged"

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception:  # noqa: BLE001 — never take the sidecar down
                    log.debug("screen memory tick failed", exc_info=True)
                self._stop.wait(self.settings.poll_s)

        self._thread = threading.Thread(target=loop, daemon=True, name="screen-memory")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        last = self.store.last()
        return {"enabled": self.settings.enabled, "paused": self.paused,
                "running": bool(self._thread and self._thread.is_alive()),
                "captures": self.store.count(), "retention_days": self.settings.retention_days,
                "last": last.as_dict() if last else None,
                "skipped": dict(self.skipped.most_common(8)),
                "allow_browsers": list(self.settings.privacy.allowed_browsers)}


def _default_redact(text: str) -> str:
    from ..core.policy import Policy, PolicyConfig

    return Policy(PolicyConfig(redact_secrets=True)).redact_text(text)
