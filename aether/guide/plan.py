"""Turn "show me how to …" into steps the user does while Aether points.

One model call returns strict JSON steps. Each step names the control to
point at (by its visible label, found later on a fresh accessibility tree)
and how to tell the user did it, so the guide advances on its own instead
of waiting for "next".
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from ..core.llm import repair_json_args

MAX_STEPS = 10
DONE_WHEN = ("click", "focus", "type", "app", "window", "menu", "any")

_GUIDE_RE = re.compile(
    r"^\s*(?:(?:hey|ok|okay)\s+aether[,!]?\s*)?(?:please\s+)?"
    r"(?:show me how(?: to| do i| i can)?|teach me(?: how)?(?: to)?|walk me through(?: how to)?|"
    r"guide me(?: through)?(?: how to)?|how do i)\s+(?P<rest>.+)$", re.I)

PLAN_SYSTEM = """You write step-by-step instructions for someone doing a task on their own Mac. \
Aether will point at each control on screen and wait for them to act.

Reply with only JSON:
{"steps": [{"say": "…", "target": "…", "role": "…", "app": "…", "done_when": "…", "expect": "…"}]}

- say: one short spoken instruction ("Click Bluetooth in the sidebar").
- target: the exact visible name of the control to click or use, or "" if none (keyboard shortcuts).
- role: button, menu item, menu, tab, field, checkbox, row, link, popup, or "".
- app: the app the control is in.
- done_when: click (they click the target), focus (they put the cursor in it), type (they type into it; \
put the text in expect), app (expect names the app that should come to the front), window (expect is \
part of the new window's title), menu (they open the target menu), or any (anything changes).
- expect: see done_when; otherwise "".

Rules: 2 to 10 steps, one action each, starting from what is on screen now. Prefer visible controls \
over shortcuts. Never include steps that delete, send, buy or change security settings unless the \
user asked for exactly that; then the last step names it clearly."""


@dataclass
class GuideStep:
    say: str
    target: str = ""
    role: str = ""
    app: str = ""
    done_when: str = "any"
    expect: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_guide_request(text: str) -> bool:
    return bool(_GUIDE_RE.match(text or ""))


def guide_goal(text: str) -> str:
    """The task itself: "show me how to add a printer" → "add a printer"."""
    m = _GUIDE_RE.match(text or "")
    goal = m.group("rest") if m else (text or "")
    return goal.strip().rstrip("?.!").strip()


def recipe_steps(goal: str) -> list[GuideStep]:
    """A knowledge pack's written guide for this goal, if one matches."""
    from ..knowledge import loader

    try:
        hit = loader.verified_recipe_for(goal)
    except Exception:  # noqa: BLE001
        return []
    if not hit or not hit[2].get("guide"):
        return []
    return parse_steps_data(hit[2]["guide"])


def parse_steps_data(raw: list) -> list[GuideStep]:
    return parse_steps_obj({"steps": raw})


def parse_steps_obj(data: Any) -> list[GuideStep]:
    raw = (data or {}).get("steps") if isinstance(data, dict) else None
    steps: list[GuideStep] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        say = " ".join(str(item.get("say") or "").split())[:200]
        if not say:
            continue
        done = str(item.get("done_when") or "any").strip().lower()
        steps.append(GuideStep(
            say=say,
            target=" ".join(str(item.get("target") or "").split())[:80],
            role=str(item.get("role") or "").strip()[:30],
            app=str(item.get("app") or "").strip()[:60],
            done_when=done if done in DONE_WHEN else "any",
            expect=str(item.get("expect") or "").strip()[:120]))
        if len(steps) >= MAX_STEPS:
            break
    return steps


def parse_steps(text: str) -> list[GuideStep]:
    return parse_steps_obj(repair_json_args(text))


def plan_steps(goal: str, client: Any, *, screen_summary: str = "", pack_hint: str = "") -> list[GuideStep]:
    """Ask the model for steps; [] when it can't produce usable ones."""
    parts = [f"Task: {goal}"]
    if screen_summary:
        parts.append("On screen now:\n" + screen_summary[:6000])
    if pack_hint:
        parts.append("Notes about this app:\n" + pack_hint[:3000])
    resp = client.step(PLAN_SYSTEM, [{"role": "user", "content": "\n\n".join(parts)}], [])
    text = (getattr(resp, "text", "") or "").strip()
    if not text:
        for block in getattr(resp, "raw_content", None) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text = str(block.get("text", ""))
                break
    return parse_steps(text)
