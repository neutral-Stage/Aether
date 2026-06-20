"""Global STOP kill switch (FR-26).

Checked before every tool invocation. Triggered by hotkey, voice, or HUD button.
Phase 12: cancels in-flight LLM HTTP requests on STOP.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

_stop = threading.Event()
_listeners: list[Callable[[], None]] = []
_active_closers: list[Callable[[], None]] = []
_closers_lock = threading.Lock()
_monitor_started = False
_monitor_lock = threading.Lock()


def is_set() -> bool:
    return _stop.is_set()


def abort_event() -> threading.Event:
    """Event set when STOP is triggered; LLM clients poll or register closers."""
    return _stop


def register_http_closer(closer: Callable[[], None]) -> None:
    """Register a callable that aborts an in-flight HTTP request (e.g. httpx.close)."""
    with _closers_lock:
        _active_closers.append(closer)


def unregister_http_closer(closer: Callable[[], None]) -> None:
    with _closers_lock:
        try:
            _active_closers.remove(closer)
        except ValueError:
            pass


def _cancel_in_flight() -> None:
    with _closers_lock:
        closers = list(_active_closers)
        _active_closers.clear()
    for fn in closers:
        try:
            fn()
        except Exception:
            pass


def trigger(reason: str = "user") -> None:
    if not _stop.is_set():
        _stop.set()
        _cancel_in_flight()
        for fn in list(_listeners):
            try:
                fn()
            except Exception:
                pass
        print(f"\n🛑 STOP ({reason}) — halting agent.\n")


def reset() -> None:
    _stop.clear()
    with _closers_lock:
        _active_closers.clear()


def on_stop(callback: Callable[[], None]) -> None:
    _listeners.append(callback)


def check() -> None:
    """Raise if STOP has been triggered."""
    if _stop.is_set():
        raise StopRequested("STOP requested — action cancelled.")


class StopRequested(Exception):
    """Raised when the global kill switch is active."""


def is_stop_phrase(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in {"stop", "stop aether", "aether stop", "halt", "cancel", "abort"}


def start_hotkey_monitor(
    hotkey: str = "ctrl+shift+s",
    escape_double_tap: bool = True,
) -> None:
    """Start a background hotkey listener (pynput if available)."""
    global _monitor_started
    with _monitor_lock:
        if _monitor_started:
            return
        _monitor_started = True

    def _run() -> None:
        try:
            from pynput import keyboard
        except ImportError:
            print("[stop] pynput not installed — hotkey STOP disabled "
                  "(pip install pynput or use HUD / voice 'stop').")
            return

        combo_parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
        pressed: set[str] = set()
        last_escape = 0.0

        def _norm(key: Any) -> str | None:
            try:
                if hasattr(key, "char") and key.char:
                    return key.char.lower()
            except Exception:
                pass
            name = str(key).replace("Key.", "").lower()
            aliases = {"ctrl_l": "ctrl", "ctrl_r": "ctrl", "shift_l": "shift",
                       "shift_r": "shift", "cmd": "cmd", "cmd_l": "cmd", "cmd_r": "cmd"}
            return aliases.get(name, name)

        def on_press(key: Any) -> None:
            nonlocal last_escape
            n = _norm(key)
            if n is None:
                return
            pressed.add(n)
            if escape_double_tap and n == "esc":
                now = time.monotonic()
                if now - last_escape < 0.45:
                    trigger("escape double-tap")
                last_escape = now
            if combo_parts and all(p in pressed for p in combo_parts):
                trigger(f"hotkey {hotkey}")

        def on_release(key: Any) -> None:
            n = _norm(key)
            if n:
                pressed.discard(n)

        with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
            listener.join()

    threading.Thread(target=_run, name="aether-stop-hotkey", daemon=True).start()
