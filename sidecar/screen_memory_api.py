"""Screen memory controls: status, pause and resume, search, activity, delete,
which browsers are allowed (Safari and others whose private windows can't be
detected — see aether/screen_memory/browsers.py)."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aether import screen_memory
from aether.core.audit_log import AuditLog
from aether.core.config import load_config
from aether.screen_memory.browsers import family as browser_family
from aether.screen_memory.prefs import set_browser_allowed
from aether.screen_memory.recorder import RecorderSettings

from .auth import require_auth

router = APIRouter()


class BrowserAllowRequest(BaseModel):
    bundle_id: str
    allowed: bool = True


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
        privacy = RecorderSettings.from_raw(load_config(validate=False).raw).privacy
        return {"enabled": False, "allow_browsers": privacy.allowed_browsers}
    return await asyncio.to_thread(rec.status)


@router.post("/screen-memory/browsers")
async def set_browser(body: BrowserAllowRequest,
                      _auth: None = Depends(require_auth)) -> dict[str, Any]:
    """Allow (or stop allowing) a browser whose private windows screen memory and
    hints otherwise can't confirm — works even while screen memory is off,
    since hints use the same preference."""
    if browser_family(body.bundle_id) is None:
        raise HTTPException(400, f"not a recognised browser: {body.bundle_id}")
    await asyncio.to_thread(set_browser_allowed, body.bundle_id, body.allowed)
    allow_browsers = RecorderSettings.from_raw(load_config(validate=False).raw).privacy \
        .allowed_browsers
    rec = screen_memory.get()
    if rec is not None:
        rec.settings.privacy.allowed_browsers = list(allow_browsers)
    AuditLog.get().record(
        "screen_memory",
        summary=f"{'allowed' if body.allowed else 'no longer allowed'} {body.bundle_id}")
    return {"allow_browsers": allow_browsers}


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
