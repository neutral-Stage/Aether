"""Basic Phase 1 HUD — menu bar + always-on-top status window."""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any

from ..core import stop as stop_ctl


@dataclass
class HUDState:
    goal: str = ""
    step: str = ""
    last_action: str = ""
    status: str = "idle"  # idle | working | stopped


class HUD:
    """Thread-safe HUD updater; runs rumps + tkinter on a background thread."""

    def __init__(self, enabled: bool = True, title: str = "Aether"):
        self.enabled = enabled
        self.title = title
        self._state = HUDState()
        self._lock = threading.Lock()
        self._updates: queue.Queue[dict[str, Any]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = False
        self._tk_root = None
        self._labels: dict[str, Any] = {}

    def start(self) -> None:
        if not self.enabled or self._started:
            return
        self._started = True
        self._thread = threading.Thread(target=self._run_ui, name="aether-hud", daemon=True)
        self._thread.start()

    def stop_ui(self) -> None:
        self._updates.put({"cmd": "quit"})

    def update(
        self,
        goal: str | None = None,
        step: str | None = None,
        last_action: str | None = None,
        status: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        with self._lock:
            if goal is not None:
                self._state.goal = goal
            if step is not None:
                self._state.step = step
            if last_action is not None:
                self._state.last_action = last_action
            if status is not None:
                self._state.status = status
            snap = HUDState(
                goal=self._state.goal,
                step=self._state.step,
                last_action=self._state.last_action,
                status=self._state.status,
            )
        self._updates.put({"cmd": "refresh", "state": snap})

    def _run_ui(self) -> None:
        try:
            import tkinter as tk
            from tkinter import ttk
        except ImportError:
            print("[hud] tkinter unavailable — HUD disabled.")
            return

        root = tk.Tk()
        root.title(self.title)
        root.geometry("360x200+40+40")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        self._tk_root = root

        frm = ttk.Frame(root, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)

        status_var = tk.StringVar(value="● idle")
        goal_var = tk.StringVar(value="Goal: —")
        step_var = tk.StringVar(value="Step: —")
        action_var = tk.StringVar(value="Last: —")

        ttk.Label(frm, textvariable=status_var, font=("", 13, "bold")).pack(anchor="w")
        ttk.Label(frm, textvariable=goal_var, wraplength=320).pack(anchor="w", pady=(8, 2))
        ttk.Label(frm, textvariable=step_var, wraplength=320).pack(anchor="w", pady=2)
        ttk.Label(frm, textvariable=action_var, wraplength=320).pack(anchor="w", pady=2)

        def on_stop() -> None:
            stop_ctl.trigger("HUD button")

        stop_btn = ttk.Button(frm, text="STOP", command=on_stop)
        stop_btn.pack(pady=(12, 0), fill=tk.X)

        self._labels = {
            "status": status_var,
            "goal": goal_var,
            "step": step_var,
            "action": action_var,
        }

        def _status_dot(s: str) -> str:
            colors = {"idle": "● idle", "working": "● working", "stopped": "● STOPPED"}
            return colors.get(s, f"● {s}")

        def pump() -> None:
            try:
                while True:
                    msg = self._updates.get_nowait()
                    if msg.get("cmd") == "quit":
                        root.destroy()
                        return
                    if msg.get("cmd") == "refresh":
                        st: HUDState = msg["state"]
                        status_var.set(_status_dot(st.status))
                        goal_var.set(f"Goal: {st.goal[:120] or '—'}")
                        step_var.set(f"Step: {st.step[:120] or '—'}")
                        action_var.set(f"Last: {st.last_action[:120] or '—'}")
            except queue.Empty:
                pass
            if root.winfo_exists():
                root.after(120, pump)

        pump()

        # Optional menu bar icon via rumps (non-blocking)
        rumps_thread = threading.Thread(target=self._run_rumps_menu, daemon=True)
        rumps_thread.start()

        root.mainloop()

    def _run_rumps_menu(self) -> None:
        try:
            import rumps
        except ImportError:
            return

        hud = self

        class AetherApp(rumps.App):
            def __init__(self):
                super().__init__(hud.title, quit_button=None)
                self.menu = [
                    rumps.MenuItem("Show HUD window", callback=self._noop),
                    rumps.MenuItem("STOP", callback=self._stop),
                    None,
                    rumps.MenuItem("Quit Aether HUD", callback=self._quit),
                ]

            def _stop(self, _sender) -> None:
                stop_ctl.trigger("menu bar STOP")

            def _quit(self, _sender) -> None:
                hud.stop_ui()
                rumps.quit_application()

            def _noop(self, _sender) -> None:
                pass

        AetherApp().run()
