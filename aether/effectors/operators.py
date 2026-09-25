"""Operators: the best way to drive the app in front (UI-TARS / Agent S3 idea).

Aether can act on an app through several channels. Which one works best
depends on the app, so the agent is told which to use first:

- ``script``: AppleScript/JXA or Shortcuts, for scriptable apps (Mail,
  Keynote, Music). Exact and fast for data.
- ``ax``: the accessibility tree, clicking by name (native Cocoa apps).
- ``browser``: the page's DOM over the Chrome DevTools protocol, for
  Chromium browsers Aether is attached to.
- ``code``: command-line tools and coding agents (VS Code, Xcode).
- ``vision``: screenshots, OCR and grounded clicks, for canvas, Electron and
  game UIs whose accessibility tree is thin.

The choice starts from the app's knowledge pack tier and is corrected by
what the accessibility tree actually shows right now.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Operator:
    name: str
    actions: tuple[str, ...]      # the tools to reach for, best first
    guidance: str


OPERATORS: dict[str, Operator] = {
    "script": Operator(
        "script", ("run_applescript", "shortcuts_run", "menu_item"),
        "It is scriptable: for data (messages, events, slides, tracks, files) use "
        "run_applescript, following the pack's recipes, instead of clicking through the UI."),
    "ax": Operator(
        "ax", ("get_screen_context", "click_element", "menu_item", "press_key", "type_text"),
        "Its controls are readable: look with get_screen_context, click by name with "
        "click_element, and prefer menu_item and keyboard shortcuts where they exist."),
    "browser": Operator(
        "browser", ("browser_attach", "browser_get_text", "browser_click", "browser_fill",
                    "browser_navigate"),
        "Work on the page through the browser tools (DOM selectors and page text), not by "
        "clicking pixels."),
    "code": Operator(
        "code", ("run_shell", "delegate_to_coder", "spawn_agent"),
        "For code changes use delegate_to_coder or spawn_agent, and command-line tools "
        "with run_shell; drive the editor's UI only for what they cannot do."),
    "vision": Operator(
        "vision", ("screenshot", "click_text", "mark_screen", "click_mark", "click_described"),
        "Its accessibility tree shows little: look with screenshot, click visible text with "
        "click_text, else mark_screen then click_mark, else click_described. Keyboard "
        "shortcuts are often the most reliable."),
}

TIER_OPERATOR = {0: "code", 1: "script", 2: "ax", 3: "vision"}
CHROMIUM_BROWSERS = frozenset({"google chrome", "chromium", "brave browser", "microsoft edge",
                               "arc", "vivaldi", "opera"})
_INTERACTIVE = ("button", "link", "field", "checkbox", "radio", "popup", "menu", "tab",
                "slider", "cell", "row", "combo", "switch", "text")
MIN_LABELLED = 5
MIN_LABELLED_RATIO = 0.3


@dataclass(frozen=True)
class Choice:
    primary: Operator
    fallbacks: tuple[Operator, ...]
    reason: str

    def prompt(self, app: str) -> str:
        then = ", then ".join(o.name for o in self.fallbacks)
        tools = ", ".join(self.primary.actions)
        text = (f"How to operate {app or 'this app'}: {self.primary.name} ({self.reason}). "
                f"{self.primary.guidance} Reach for: {tools}.")
        return text + (f" If that fails: {then}." if then else "")


def ax_richness(elements: list[Any]) -> tuple[int, float]:
    """(labelled interactive elements, their share of all elements)."""
    if not elements:
        return 0, 0.0
    labelled = 0
    for el in elements:
        get = el.get if isinstance(el, dict) else (lambda k, _e=el: getattr(_e, k, ""))
        role = str(get("role") or "").lower()
        label = str(get("title") or get("value") or get("help") or "").strip()
        if label and any(word in role for word in _INTERACTIVE):
            labelled += 1
    return labelled, labelled / len(elements)


def choose(app: str, elements: list[Any], pack: dict | None = None, *,
           browser_attach_mode: str = "headless", element_count: int | None = None) -> Choice:
    """Pick the primary operator and its fallbacks for the frontmost app."""
    app_l = (app or "").strip().lower()
    labelled, ratio = ax_richness(elements)
    count = len(elements) if element_count is None else element_count
    thin = count == 0 or labelled < MIN_LABELLED or ratio < MIN_LABELLED_RATIO

    if app_l in CHROMIUM_BROWSERS and browser_attach_mode == "cdp":
        return Choice(OPERATORS["browser"], (OPERATORS["ax"], OPERATORS["vision"]),
                      "a Chromium browser Aether is attached to")

    tier = (pack or {}).get("tier")
    preferred = TIER_OPERATOR.get(tier) if isinstance(tier, int) else None
    has_script = bool((pack or {}).get("scripting"))

    if preferred == "vision" and not thin:
        return Choice(OPERATORS["ax"], (OPERATORS["vision"],),
                      "its pack expects a thin accessibility tree, but this screen's is rich")
    if preferred in ("script", "code"):
        rest = (OPERATORS["vision"], OPERATORS["ax"]) if thin else \
            (OPERATORS["ax"], OPERATORS["vision"])
        return Choice(OPERATORS[preferred], rest,
                      "scriptable app" if preferred == "script" else "a developer tool")
    if thin:
        rest = (OPERATORS["script"],) if has_script else ()
        why = ("nothing readable on screen" if count == 0 else
               f"only {labelled} labelled controls on screen")
        return Choice(OPERATORS["vision"], (*rest, OPERATORS["ax"]), why)
    rest = ((OPERATORS["script"],) if has_script else ()) + (OPERATORS["vision"],)
    return Choice(OPERATORS["ax"], rest,
                  "its pack says the accessibility tree works" if preferred == "ax"
                  else f"{labelled} labelled controls on screen")
