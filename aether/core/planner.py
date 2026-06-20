"""Explicit planning phase for novel goals (Phase 11, FR-9)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from .world_model import WorldModel


class PlanLLM(Protocol):
    def step(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any: ...


_PLAN_SYSTEM = """You are a task planner for a macOS automation agent.
Given a user goal, output a JSON array of 3-8 concrete steps the agent should take.
Each step is a short imperative phrase (e.g. "Open Safari", "Navigate to apple.com").
Respond with ONLY a JSON array of strings, no markdown."""


@dataclass
class PlanResult:
    steps: list[str]
    source: str  # heuristic | llm | cached | replay


def plan_goal(
    goal: str,
    world: WorldModel,
    *,
    llm: PlanLLM | None = None,
    use_llm: bool = True,
    max_steps: int = 8,
) -> PlanResult:
    """Decompose a goal into steps. Uses cached plan when world already has one."""
    if world.plan and not world.needs_replan:
        return PlanResult(steps=list(world.plan), source="cached")

    if use_llm and llm is not None:
        steps = _plan_with_llm(goal, world, llm, max_steps=max_steps)
        if steps:
            world.set_plan(steps)
            return PlanResult(steps=steps, source="llm")

    steps = _plan_heuristic(goal, max_steps=max_steps)
    world.set_plan(steps)
    return PlanResult(steps=steps, source="heuristic")


def replan(
    goal: str,
    world: WorldModel,
    *,
    llm: PlanLLM | None = None,
    use_llm: bool = True,
    failure_context: str = "",
) -> PlanResult:
    """Re-plan after verification failure."""
    world.plan = []
    world.is_novel_goal = True
    hint = goal
    if failure_context:
        hint = f"{goal}\n\nPrevious attempt failed: {failure_context}"
    return plan_goal(hint, world, llm=llm, use_llm=use_llm)


def _plan_heuristic(goal: str, *, max_steps: int = 8) -> list[str]:
    """Rule-based decomposition when LLM unavailable."""
    text = (goal or "").strip()
    if not text:
        return ["Understand the request", "Execute actions", "Verify result", "Finish"]

    # Numbered list in goal
    numbered = re.findall(r"(?:^|\n)\s*\d+[.)]\s*(.+)", text)
    if len(numbered) >= 2:
        return [s.strip() for s in numbered[:max_steps]]

    # "then" / "and then" splits
    parts = re.split(r"\s+then\s+|\s+and then\s+", text, flags=re.IGNORECASE)
    if len(parts) >= 2:
        steps = [p.strip().rstrip(".") for p in parts if p.strip()]
        if len(steps) >= 2:
            return steps[:max_steps]

    # " and " split for compound goals
    and_parts = re.split(r"\s+and\s+", text, maxsplit=3)
    if len(and_parts) >= 2 and all(len(p) > 5 for p in and_parts):
        steps = [p.strip().rstrip(".") for p in and_parts]
        steps.append("Verify the result")
        return steps[:max_steps]

    return [
        f"Understand goal: {text[:80]}",
        "Perceive current screen state",
        "Execute required actions",
        "Verify outcome",
        "Report completion",
    ][:max_steps]


def _plan_with_llm(
    goal: str,
    world: WorldModel,
    llm: PlanLLM,
    *,
    max_steps: int = 8,
) -> list[str]:
    ctx = world.context_block()
    user_msg = f"Goal: {goal}"
    if ctx:
        user_msg += f"\n\nContext:\n{ctx}"
    try:
        resp = llm.step(_PLAN_SYSTEM, [{"role": "user", "content": user_msg}], [])
    except Exception:  # noqa: BLE001
        return []

    text = (getattr(resp, "text", None) or "").strip()
    if not text and getattr(resp, "raw_content", None):
        for block in resp.raw_content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                break

    steps = _parse_plan_json(text)
    return steps[:max_steps] if steps else []


def _parse_plan_json(text: str) -> list[str]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(s).strip() for s in data if str(s).strip()]
    except json.JSONDecodeError:
        pass
    # Fallback: line-based bullets
    lines = [
        re.sub(r"^[-*•\d.)]+\s*", "", ln).strip()
        for ln in text.splitlines()
        if ln.strip()
    ]
    return [ln for ln in lines if len(ln) > 2]
