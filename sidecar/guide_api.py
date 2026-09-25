"""Guide mode endpoints: plan the steps, run the guide, take controls.

POST /guide {goal}                → plans steps from what is on screen, starts the guide
POST /guide/{id} {action}         → next | back | repeat | skip | stop | do_it
GET  /guide/{id}                  → where the guide is

Progress goes out as guide_step / guide_done / guide_handoff events on
/events (and run_request for "do it for me", which the app turns into a
normal agent run under the policy gate).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aether.core.audit_log import AuditLog
from aether.core.config import load_config
from aether.core.policy import Policy, PolicyConfig
from aether.guide import plan
from aether.guide.detect import ClickWatcher
from aether.guide.session import ACTIONS, GuideSession

from . import talk_api
from .auth import require_auth
from .errors import redact_error_message

log = logging.getLogger(__name__)
router = APIRouter()
_broadcast = None
_GUIDES: dict[str, GuideSession] = {}
_TASKS: dict[str, asyncio.Task] = {}


def set_broadcaster(fn) -> None:  # noqa: ANN001
    global _broadcast
    _broadcast = fn


async def _emit(event: dict) -> None:
    if _broadcast is not None:
        await _broadcast(event)


class GuideRequest(BaseModel):
    goal: str


class GuideControl(BaseModel):
    action: str


def _screen_summary(redact) -> tuple[str, str]:  # noqa: ANN001
    """(what is on screen, pack notes for the front app)."""
    from aether.knowledge import loader as knowledge
    from aether.perception import accessibility as ax

    try:
        ctx = ax.screen_context(max_elements=60, capture_handles=False)
    except Exception:  # noqa: BLE001
        return "", ""
    front = str(ctx.get("frontmost_app", ""))
    summary = redact(f"Front app: {front}\n{ctx.get('rendered', '')}")
    try:
        hint = knowledge.prompt_slice(front, "", bundle_id=str(ctx.get("bundle_id", "")))
    except Exception:  # noqa: BLE001
        hint = ""
    return summary, hint


@router.post("/guide")
async def start_guide(body: GuideRequest, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    goal = plan.guide_goal(" ".join((body.goal or "").split()))
    if not goal:
        raise HTTPException(400, "goal is required")
    cfg = load_config()
    for other in list(_GUIDES.values()):          # one guide at a time
        other.control("stop")
    source = "recipe"
    steps = plan.recipe_steps(goal)               # a pack's written guide, when one fits
    if not steps:
        source = "model"
        if not cfg.has_cloud_llm():
            raise HTTPException(400, "Guide mode needs a cloud model key "
                                     "(see configs/router.yaml).")
        policy = Policy(PolicyConfig(redact_secrets=True))
        try:
            summary, hint = await asyncio.to_thread(_screen_summary, policy.redact_text)
            client = talk_api._client(cfg)  # noqa: SLF001
            steps = await asyncio.to_thread(plan.plan_steps, goal, client,
                                            screen_summary=summary, pack_hint=hint)
        except Exception as e:  # noqa: BLE001
            log.warning("guide planning failed", exc_info=True)
            raise HTTPException(502, "Could not plan the guide: "
                                     f"{redact_error_message(str(e))}") from e
    if not steps:
        raise HTTPException(422, "Couldn't work out the steps for that. Try asking Aether to do it.")
    clicks = ClickWatcher()
    await asyncio.to_thread(clicks.start)
    session = GuideSession(goal, steps, emit=_emit, clicks=clicks)
    _GUIDES[session.id] = session
    _TASKS[session.id] = asyncio.create_task(session.run())
    try:
        audit_cfg = cfg.get("audit") or {}
        AuditLog.get(path=audit_cfg.get("path"), enabled=bool(audit_cfg.get("enabled", True))) \
            .record("guide_start", summary=goal[:200], extra={"steps": len(steps)})
    except Exception:  # noqa: BLE001
        pass
    return {**session.state(), "click_detection": clicks.running, "source": source}


@router.post("/guide/{guide_id}")
async def control_guide(guide_id: str, body: GuideControl,
                        _auth: None = Depends(require_auth)) -> dict[str, Any]:
    session = _GUIDES.get(guide_id)
    if session is None:
        raise HTTPException(404, "Unknown guide")
    if body.action not in ACTIONS:
        raise HTTPException(400, f"action must be one of {', '.join(ACTIONS)}")
    if not session.control(body.action):
        raise HTTPException(409, f"The guide is {session.status}")
    return session.state()


@router.get("/guide/{guide_id}")
async def get_guide(guide_id: str, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    session = _GUIDES.get(guide_id)
    if session is None:
        raise HTTPException(404, "Unknown guide")
    return session.state()
