"""POST /talk: point at something and ask (talk mode, aether/talk.py).

Returns the spoken answer and the resolved pointing targets (global screen
points) for the app's overlay. The same targets are broadcast as a
``pointer`` event on /events so any connected overlay can show them.
"""
from __future__ import annotations

import asyncio
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

from .auth import require_auth
from .errors import redact_error_message

log = logging.getLogger(__name__)
router = APIRouter()
_broadcast = None   # set by server.py: async fn(event)


def set_broadcaster(fn) -> None:  # noqa: ANN001
    global _broadcast
    _broadcast = fn


class TalkRequest(BaseModel):
    question: str
    x: float | None = None          # pointer position (global points); default: live cursor
    y: float | None = None
    session_id: str | None = None   # keep follow-ups in one talk conversation
    refine: bool = True
    stream: bool = True             # broadcast talk_token events while answering
    talk_id: str | None = None      # echoed in the events, so the app can match them


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

    def on_text(text: str) -> None:
        # The answer as it streams (tags removed), so the app can speak early.
        if broadcast is not None:
            asyncio.run_coroutine_threadsafe(
                broadcast({"type": "talk_token", "talk_id": talk_id, "text": text}), loop)

    try:
        client = _client(cfg)
        reply = await asyncio.to_thread(talk.ask, question, client, cursor=cursor,
                                        session_id=body.session_id, refine=body.refine,
                                        redact=policy.redact_text,
                                        on_text=on_text if body.stream else None)
    except Exception as e:  # noqa: BLE001
        log.warning("talk failed", exc_info=True)
        raise HTTPException(502, f"Talk failed: {redact_error_message(str(e))}") from e

    metrics = MetricsCollector.get()
    metrics.observe("talk_ms", reply.talk_ms)
    if reply.first_text_ms is not None:
        metrics.observe("talk_first_text_ms", reply.first_text_ms)
    metrics.inc("talk_requests")
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
