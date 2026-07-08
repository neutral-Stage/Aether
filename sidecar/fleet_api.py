"""Fleet HTTP API — spawn/monitor/steer/stop agent sessions (Phase 2).

Sessions live outside RunState so agents keep running between Aether runs.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from aether.fleet.manager import SessionManager

from .auth import require_auth
from .rate_limit import get_limiter

router = APIRouter(prefix="/fleet", tags=["fleet"])


class SpawnRequest(BaseModel):
    agent_type: str
    prompt: str
    workspace: str | None = None
    label: str | None = None
    isolate: bool | None = None
    timeout_sec: int | None = None


class SendRequest(BaseModel):
    text: str


def _rate_limit(request: Request, bucket: str) -> None:
    key = request.client.host if request.client else "unknown"
    ok, retry_after = get_limiter().allow(bucket, key)
    if not ok:
        raise HTTPException(429, f"Rate limit exceeded for {bucket}.",
                            headers={"Retry-After": str(int(retry_after) + 1)})


def _session_or_404(session_id: str):
    session = SessionManager.get().resolve(session_id)
    if session is None:
        raise HTTPException(404, f"no session matching {session_id!r}")
    return session


@router.get("")
async def list_sessions() -> dict[str, Any]:
    mgr = SessionManager.get()
    return {"sessions": mgr.list(), **mgr.snapshot()}


# NOTE: /graphs routes MUST precede /{session_id} so "graphs" isn't captured as an id.
@router.get("/graphs")
async def list_graphs() -> dict[str, Any]:
    from aether.fleet.graph_runner import GraphManager
    return {"graphs": GraphManager.get().list()}


@router.get("/graphs/{graph_id}")
async def get_graph(graph_id: str) -> dict[str, Any]:
    from aether.fleet.graph_runner import GraphManager
    graph = GraphManager.get().resolve(graph_id)
    if graph is None:
        raise HTTPException(404, f"no graph matching {graph_id!r}")
    return graph.to_dict()


@router.get("/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    return _session_or_404(session_id).status_dict()


@router.get("/{session_id}/output")
async def get_output(
    session_id: str, since_seq: int = 0, tail: int = 100,
) -> dict[str, Any]:
    session = _session_or_404(session_id)
    events = (
        session.events_since(since_seq) if since_seq > 0 else session.tail(tail)
    )
    return {
        "session": session.status_dict(),
        "events": [e.to_dict() for e in events],
    }


@router.post("/spawn")
async def spawn(
    body: SpawnRequest, request: Request, _auth: None = Depends(require_auth),
) -> dict[str, Any]:
    _rate_limit(request, "fleet_spawn")
    try:
        session = await asyncio.to_thread(
            lambda: SessionManager.get().spawn(
                agent_type=body.agent_type,
                prompt=body.prompt,
                workspace=body.workspace,
                label=body.label,
                isolate=body.isolate,
                timeout_sec=body.timeout_sec,
            )
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return session.status_dict()


@router.post("/stop_all")
async def stop_all(_auth: None = Depends(require_auth)) -> dict[str, Any]:
    count = await asyncio.to_thread(SessionManager.get().stop_all)
    return {"stopped": count}


@router.post("/{session_id}/send")
async def send(
    session_id: str, body: SendRequest, _auth: None = Depends(require_auth),
) -> dict[str, Any]:
    session = _session_or_404(session_id)
    ok = await asyncio.to_thread(SessionManager.get().send, session.session_id, body.text)
    return {"delivered": ok, "session": session.status_dict()}


@router.post("/{session_id}/stop")
async def stop(
    session_id: str, _auth: None = Depends(require_auth),
) -> dict[str, Any]:
    session = _session_or_404(session_id)
    await asyncio.to_thread(SessionManager.get().stop, session.session_id)
    return session.status_dict()


def register_sink(
    loop: asyncio.AbstractEventLoop,
    broadcast: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    """Bridge fleet reader threads → sidecar SSE subscribers (+ spoken finish)."""

    def _sink(payload: dict[str, Any]) -> None:
        events: list[dict[str, Any]] = [payload]
        if payload.get("event") == "status" and payload.get("state") in (
            "done", "error", "timeout",
        ):
            session = SessionManager.get().resolve(str(payload.get("session_id")))
            summary = (session.result_summary if session else "") or payload.get("state")
            events.append({
                "type": "say",
                "text": f"Agent {payload.get('label')} finished: {str(summary)[:200]}",
            })

        def _put() -> None:
            for ev in events:
                asyncio.ensure_future(broadcast(ev))

        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:
            pass  # loop shut down

    SessionManager.get().set_event_sink(_sink)

    # Graph orchestration events → SSE (+ spoken summary on completion).
    def _graph_sink(payload: dict[str, Any]) -> None:
        # Durable state so a restart flips a mid-flight graph to 'interrupted'.
        from . import run_store
        gid = str(payload.get("graph_id", ""))
        if payload.get("event") == "started":
            run_store.append("graph", gid, "running")
        elif payload.get("event") in ("complete", "failed"):
            run_store.append("graph", gid, str(payload.get("status", "failed")))

        events: list[dict[str, Any]] = [payload]
        if payload.get("event") == "complete":
            events.append({
                "type": "say",
                "text": f"Task graph finished: {str(payload.get('result', ''))[:200]}",
            })

        def _put() -> None:
            for ev in events:
                asyncio.ensure_future(broadcast(ev))

        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:
            pass

    from aether.fleet.graph_runner import GraphManager
    GraphManager.get().set_event_sink(_graph_sink)
