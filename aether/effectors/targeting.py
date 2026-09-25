"""Find and click UI elements by NAME on a fresh accessibility tree.

Element indices from get_screen_context go stale the moment the UI changes
(a sheet opens, a list scrolls), so an index-based click can hit the wrong
control. click_element re-reads the tree at execution time and picks the best
match for a name — the cursor-voice approach, the most reliable open-source Mac
agent studied — with AXPress first and a center click as the fallback.

Scoring (case-insensitive, whitespace/ellipsis normalised):
  exact title 1000 > title prefix 800 > title contains 600−extra chars (≥450)
  > value equal 420 > value contains 400 > identifier/help/role contains 350
  > fuzzy bigram coverage ≥ 0.55 (≤330). +50 when the requested role matches,
  +20 for actionable roles, −150 when disabled. Ties prefer the smaller element.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..perception import accessibility as ax

MIN_SCORE = 300

ROLE_ALIASES = {
    "button": "AXButton", "link": "AXLink", "field": "AXTextField",
    "text field": "AXTextField", "textfield": "AXTextField", "text area": "AXTextArea",
    "checkbox": "AXCheckBox", "check box": "AXCheckBox", "toggle": "AXCheckBox",
    "switch": "AXCheckBox", "tab": "AXTab", "menu item": "AXMenuItem",
    "menu button": "AXMenuButton", "popup": "AXPopUpButton", "pop up button": "AXPopUpButton",
    "dropdown": "AXPopUpButton", "radio": "AXRadioButton", "radio button": "AXRadioButton",
    "row": "AXRow", "cell": "AXCell", "image": "AXImage", "icon": "AXImage",
    "text": "AXStaticText", "label": "AXStaticText", "search": "AXSearchField",
    "search field": "AXSearchField", "slider": "AXSlider", "combo box": "AXComboBox",
    "combobox": "AXComboBox", "segmented control": "AXSegmentedControl",
}
_ACTIONABLE = {"AXButton", "AXLink", "AXMenuItem", "AXMenuButton", "AXCheckBox",
               "AXRadioButton", "AXPopUpButton", "AXTab", "AXTextField", "AXTextArea",
               "AXSearchField", "AXComboBox", "AXToolbarButton", "AXDisclosureTriangle",
               "AXSegmentedControl", "AXCell", "AXRow", "AXSlider"}
_ELLIPSIS_RE = re.compile(r"(\.\.\.|…)")


def _norm(s: str) -> str:
    return " ".join(_ELLIPSIS_RE.sub("", str(s or "")).casefold().split())


def _bigrams(s: str) -> set[str]:
    s = s.replace(" ", "")
    return {s[i:i + 2] for i in range(len(s) - 1)}


def bigram_coverage(query: str, text: str) -> float:
    q = _bigrams(_norm(query))
    if not q:
        return 0.0
    return len(q & _bigrams(_norm(text))) / len(q)


def role_matches(el_role: str, wanted: str | None) -> bool:
    if not wanted:
        return True
    key = wanted.strip().casefold()
    target = ROLE_ALIASES.get(key)
    if target is None:
        # "MenuItem", "AXMenuItem", "menu_item": compare without prefix, case or spacing.
        bare = key.removeprefix("ax").replace(" ", "").replace("_", "")
        return el_role.casefold().removeprefix("ax") == bare
    if target == "AXButton" and el_role in ("AXButton", "AXToolbarButton", "AXMenuButton"):
        return True
    return el_role == target


def score(el: ax.Element, query: str, role: str | None = None) -> int:
    q = _norm(query)
    if not q or not role_matches(el.role, role):
        return 0
    title, value = _norm(el.title), _norm(el.value)
    best = 0
    if title == q:
        best = 1000
    elif title.startswith(q):
        best = 800
    elif q in title:
        best = max(600 - (len(title) - len(q)), 450)
    elif value == q:
        best = 420
    elif q in value:
        best = 400
    elif q in _norm(" ".join([el.identifier, el.help, el.role.removeprefix("AX")])):
        best = 350
    else:
        cov = max(bigram_coverage(q, title), bigram_coverage(q, el.help))
        if cov >= 0.55:
            best = int(330 * cov)
    if not best:
        return 0
    if role:
        best += 50
    if el.role in _ACTIONABLE:
        best += 20
    if not el.enabled:
        best -= 150
    return best


@dataclass
class Match:
    element: ax.Element
    handle: object | None
    score: int
    runner_up: ax.Element | None = None
    ambiguous: bool = False


def rank(elements: list[ax.Element], query: str, role: str | None = None) -> list[tuple[int, ax.Element]]:
    scored = [(score(e, query, role), e) for e in elements]
    scored = [(s, e) for s, e in scored if s > 0]
    scored.sort(key=lambda se: (-se[0], se[1].w * se[1].h))
    return scored


def find(query: str, role: str | None = None, app: str | None = None,
         max_elements: int = 250) -> tuple[Match | None, list[ax.Element], str]:
    """Best match on a fresh tree → (match or None, candidates, app name)."""
    if app:
        info = ax.resolve_app(app)
        if info is None:
            raise RuntimeError(f"app not running: {app}")
        pid, app_name = int(info["pid"]), str(info["name"])
    else:
        info = ax.frontmost_app()
        pid, app_name = int(info.get("pid", -1)), str(info.get("name", ""))
    handles: dict[int, object] = {}
    elements = ax.read_tree(max_elements=max_elements, capture_handles=True,
                            pid=pid if pid > 0 else None, handles_out=handles)
    ranked = rank(elements, query, role)
    if not ranked or ranked[0][0] < MIN_SCORE:
        return None, [e for _, e in ranked[:5]], app_name
    top_score, top = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else None
    ambiguous = bool(runner and ranked[1][0] == top_score
                     and _norm(runner.title) == _norm(top.title)
                     and (abs(runner.x - top.x) > 2 or abs(runner.y - top.y) > 2))
    return (Match(top, handles.get(top.idx), top_score, runner, ambiguous),
            [e for _, e in ranked[:5]], app_name)


def label_of(el: ax.Element) -> str:
    return el.title or el.value or el.identifier or el.role.removeprefix("AX")


def click_element(query: str, role: str | None = None, app: str | None = None,
                  double: bool = False, button: str = "left") -> str:
    from . import ax_actions, executor
    from . import input as kbd

    match, candidates, app_name = find(query, role, app)
    if match is None:
        hint = ", ".join(f"'{label_of(e)}' ({e.role.removeprefix('AX')})" for e in candidates)
        return (f"ERROR: no element named '{query}'"
                + (f" ({role})" if role else "") + f" in {app_name or 'the front app'}."
                + (f" Closest: {hint}." if hint else
                   " Call get_screen_context, scroll, or try click_text / mark_screen."))
    el = match.element
    what = f"'{label_of(el)}' ({el.role.removeprefix('AX')})"
    guard = sensitivity_guard(query, label_of(el), "click_element", "name")
    if guard:
        return guard
    note = " Note: several elements share this name; picked the smallest — pass role= to narrow." \
        if match.ambiguous else ""
    if not el.enabled:
        return f"ERROR: {what} is disabled right now."
    if button == "left" and not double and match.handle is not None:
        try:
            ax_actions.press_handle(match.handle, label=what)
            return f"Clicked {what} via AXPress.{note}"
        except Exception:  # noqa: BLE001 — fall back to a real click
            pass
    cx, cy = el.center
    count = 2 if double else 1
    front = ax.frontmost_app().get("name", "")
    if app and app_name and app_name != front:
        info = ax.resolve_app(app_name)
        executor.focused_action(info["pid"], lambda: kbd.click(cx, cy, button=button, count=count))
    else:
        with executor.HID_LOCK:
            kbd.click(cx, cy, button=button, count=count)
    return f"Clicked {what} at ({int(cx)}, {int(cy)}).{note}"


def sensitivity_guard(requested: str, resolved: str, tool: str, arg: str) -> str | None:
    """Refuse when a fuzzy match lands on a control the gate would have
    confirmed but the requested name would not have triggered ("Trash" ->
    "Empty Trash", "Continue" -> "Buy now"). The model must name it exactly,
    so the confirmation shows what is really about to be pressed."""
    from ..core.policy import is_sensitive_label

    if is_sensitive_label(resolved) and not is_sensitive_label(requested):
        return (f"ERROR: the closest match for '{requested}' is '{resolved[:80]}', which "
                f"needs confirmation. If that is the right control, call {tool} again "
                f"with {arg}='{resolved[:80]}'.")
    return None


def current_label(idx: int, elements: list[dict]) -> str | None:
    """Label of element [idx]: live from its AX handle, else from the last read."""
    from . import ax_actions

    live = ax.handle_label(ax_actions._element_handles.get(int(idx)))  # noqa: SLF001
    if live:
        return live
    for el in elements or []:
        if el.get("idx") == int(idx):
            return str(el.get("title") or el.get("value") or el.get("identifier") or "")
    return None


def label_consistent(el_label: str, expected: str) -> bool:
    """Does the element at a remembered index still carry the expected label?"""
    a, b = _norm(el_label), _norm(expected)
    if not b:
        return True
    return b == a or b in a or a in b or bigram_coverage(b, a) >= 0.7
