"""Screen memory controls: status, pause and resume, search, activity, delete."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from aether import screen_memory
from aether.core.audit_log import AuditLog

from .auth import require_auth

router = APIRouter()


def _rec():  # noqa: ANN202
    rec = screen_memory.get()
    if rec is None:
        raise HTTPException(409, "Screen memory is off (screen_memory.enabled in config.yaml).")
    return rec


def start_if_enabled() -> bool:
    """Called at sidecar start."""
    rec = screen_memory.get()
    if rec is None:
        return False
    rec.start()
    return True


@router.get("/screen-memory/status")
async def status(_auth: None = Depends(require_auth)) -> dict[str, Any]:
    rec = screen_memory.get()
    if rec is None:
        return {"enabled": False}
    return await asyncio.to_thread(rec.status)


@router.post("/screen-memory/pause")
async def pause(_auth: None = Depends(require_auth)) -> dict[str, Any]:
    rec = _rec()
    rec.pause()
    AuditLog.get().record("screen_memory", summary="paused")
    return {"paused": True}


@router.post("/screen-memory/resume")
async def resume(_auth: None = Depends(require_auth)) -> dict[str, Any]:
    rec = _rec()
    rec.resume()
    AuditLog.get().record("screen_memory", summary="resumed")
    return {"paused": False}


@router.delete("/screen-memory")
async def delete(minutes: float | None = None,
                 _auth: None = Depends(require_auth)) -> dict[str, Any]:
    """Delete the last `minutes` of captures, or everything when omitted."""
    if minutes is not None and not minutes > 0:
        # 0 or a negative number is a mistake, not "delete everything".
        raise HTTPException(400, "minutes must be more than 0; leave it out to delete all")
    rec = _rec()
    since = time.time() - minutes * 60 if minutes is not None else None
    removed = await asyncio.to_thread(rec.store.delete, since=since)
    AuditLog.get().record("screen_memory", summary=f"deleted {removed} capture(s)"
                          + (f" from the last {minutes:g} min" if minutes is not None
                             else " (all)"))
    return {"deleted": removed}


@router.get("/screen-memory/search")
async def search(q: str = "", hours: float = 24, app: str = "", limit: int = 20,
                 _auth: None = Depends(require_auth)) -> dict[str, Any]:
    rec = _rec()
    hits = await asyncio.to_thread(rec.store.search, q, since=time.time() - hours * 3600,
                                   app=app, limit=max(1, min(limit, 100)))
    return {"results": [h.as_dict() for h in hits]}


@router.get("/screen-memory/activity")
async def activity(hours: float = 8, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    rec = _rec()
    return {"apps": await asyncio.to_thread(rec.store.activity, time.time() - hours * 3600)}
