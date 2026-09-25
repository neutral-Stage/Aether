"""POST /talk: point at something and ask (talk mode, aether/talk.py).

Returns the spoken answer and the resolved pointing targets (global screen
points) for the app's overlay. The same targets are broadcast as a
``pointer`` event on /events so any connected overlay can show them.
"""
from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
import uuid
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aether import talk
from aether.core.audit_log import AuditLog
from aether.core.config import ROOT, load_config
from aether.core.metrics import MetricsCollector
from aether.core.policy import Policy, PolicyConfig
from aether.core.router import Router, RouterConfig, RouteTier
from aether.core.stop import StopRequested

from .auth import require_auth
from .errors import redact_error_message

log = logging.getLogger(__name__)
router = APIRouter()
_broadcast = None   # set by server.py: async fn(event)

# talk_id -> the Event that cancels it (POST /talk/{id}/cancel, or STOP via
# cancel_all()). Guarded by _active_lock; entries are removed once the talk
# ends, cancelled or not.
_active: dict[str, threading.Event] = {}
_active_lock = threading.Lock()
# Recently finished speculative talks: talk_id -> (session_id, question, answer).
# A speculation can finish before the user lets go; if their final words then
# differ, the app cancels it late, and its turn is taken back out of the history.
_finished_speculative: OrderedDict[str, tuple[str, str, str]] = OrderedDict()
_MAX_FINISHED = 20


def set_broadcaster(fn) -> None:  # noqa: ANN001
    global _broadcast
    _broadcast = fn


def cancel_all() -> int:
    """Cancel every talk currently in flight (called by /stop). Returns how many."""
    with _active_lock:
        events = list(_active.values())
    for event in events:
        event.set()
    return len(events)


class TalkRequest(BaseModel):
    question: str
    x: float | None = None          # pointer position (global points); default: live cursor
    y: float | None = None
    session_id: str | None = None   # keep follow-ups in one talk conversation
    refine: bool = True
    stream: bool = True             # broadcast talk_token events while answering
    talk_id: str | None = None      # echoed in the events, so the app can match them
    speculative: bool = False       # fired before push-to-talk was released; may be wasted


def _client(cfg: Any) -> Any:
    router_path = cfg.get("router", "config_path")
    r = Router(router_cfg=RouterConfig.load(ROOT / router_path if router_path else None),
               anthropic_api_key=cfg.anthropic_api_key, api_keys=cfg.api_keys)
    return r.pick_client_with_failover(RouteTier.CLOUD_FRONTIER)


@router.post("/talk")
async def talk_endpoint(body: TalkRequest, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    question = " ".join((body.question or "").split())
    if not question:
        raise HTTPException(400, "question is required")
    cfg = load_config()
    if not cfg.has_cloud_llm():
        raise HTTPException(400, "Talk mode needs a vision-capable cloud model key "
                                 "(see configs/router.yaml).")
    policy = Policy(PolicyConfig(redact_secrets=True))
    cursor = (body.x, body.y) if body.x is not None and body.y is not None else None
    talk_id = body.talk_id or uuid.uuid4().hex[:12]
    loop = asyncio.get_running_loop()
    broadcast = _broadcast
    event = threading.Event()
    with _active_lock:
        _active[talk_id] = event

    def on_text(text: str) -> None:
        # The answer as it streams (tags removed), so the app can speak early.
        if event.is_set():
            return
        if broadcast is not None:
            asyncio.run_coroutine_threadsafe(
                broadcast({"type": "talk_token", "talk_id": talk_id, "text": text}), loop)

    cancelled = False
    reply = None
    try:
        client = _client(cfg)
        reply = await asyncio.to_thread(talk.ask, question, client, cursor=cursor,
                                        session_id=body.session_id, refine=body.refine,
                                        redact=policy.redact_text,
                                        on_text=on_text if body.stream else None,
                                        abort_event=event)
    except StopRequested:
        cancelled = True
    except Exception as e:  # noqa: BLE001
        log.warning("talk failed", exc_info=True)
        raise HTTPException(502, f"Talk failed: {redact_error_message(str(e))}") from e
    finally:
        with _active_lock:
            _active.pop(talk_id, None)

    if cancelled or event.is_set():
        metrics = MetricsCollector.get()
        metrics.inc("talk_cancelled")
        if body.speculative:
            metrics.inc("talk_speculative_wasted")
        try:
            audit_cfg = cfg.get("audit") or {}
            AuditLog.get(path=audit_cfg.get("path"),
                         enabled=bool(audit_cfg.get("enabled", True))).record(
                "talk_cancelled", summary=f"Q: {question[:150]}")
        except Exception:  # noqa: BLE001 — auditing must not fail the response
            log.debug("talk_cancelled audit failed", exc_info=True)
        return {"cancelled": True, "talk_id": talk_id}

    metrics = MetricsCollector.get()
    metrics.observe("talk_ms", reply.talk_ms)
    if reply.first_text_ms is not None:
        metrics.observe("talk_first_text_ms", reply.first_text_ms)
    metrics.inc("talk_requests")
    if body.speculative:
        metrics.inc("talk_speculative_used")
        with _active_lock:
            _finished_speculative[talk_id] = (reply.session_id, question, reply.answer)
            while len(_finished_speculative) > _MAX_FINISHED:
                _finished_speculative.popitem(last=False)
    try:
        audit_cfg = cfg.get("audit") or {}
        AuditLog.get(path=audit_cfg.get("path"),
                     enabled=bool(audit_cfg.get("enabled", True))).record(
            "talk", summary=f"Q: {question[:150]} | A: {reply.answer[:150]}",
            extra={"targets": len(reply.targets), "refined": reply.refined})
    except Exception:  # noqa: BLE001 — auditing must not fail the answer
        log.debug("talk audit failed", exc_info=True)
    data = {**reply.as_dict(), "talk_id": talk_id}
    if _broadcast is not None and body.stream:
        await _broadcast({"type": "talk_done", "talk_id": talk_id, "answer": reply.answer,
                          "session_id": reply.session_id})
    if _broadcast is not None and reply.targets:
        await _broadcast({"type": "pointer", "source": "talk", "targets": data["targets"],
                          "answer": reply.answer})
    return data


@router.post("/talk/{talk_id}/cancel")
async def cancel_talk(talk_id: str, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    """Cancel a talk in flight (speculative or not). A speculative talk that already
    finished is taken back out of the talk history instead; anything else that
    already ended is a no-op."""
    with _active_lock:
        event = _active.get(talk_id)
        finished = _finished_speculative.pop(talk_id, None) if event is None else None
    if event is not None:
        event.set()
        return {"cancelled": True}
    if finished is not None:
        session_id, question, answer = finished
        talk.forget_turn(session_id, question, answer)
        metrics = MetricsCollector.get()
        metrics.inc("talk_speculative_wasted")
        return {"cancelled": True, "forgotten": True}
    return {"cancelled": False}
