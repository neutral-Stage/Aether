"""Watched-apps HTTP API (Phase 4) — list running apps, manage watchers.

Watcher events stream to SSE subscribers as {"type": "app_event", ...}.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from aether.perception import accessibility as ax
from aether.perception.app_watcher import AppWatcher

from .auth import require_auth

router = APIRouter(prefix="/apps", tags=["apps"])


class WatchRequest(BaseModel):
    app: str


@router.get("")
async def list_apps() -> dict[str, Any]:
    watcher = AppWatcher.get()
    return {
        "running": await asyncio.to_thread(ax.list_running_apps),
        "watched": watcher.watched(),
        "recent_events": watcher.recent_events(limit=10),
    }


@router.post("/watch")
async def watch(req: WatchRequest, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    result = await asyncio.to_thread(AppWatcher.get().watch, req.app)
    return {"result": result, "watched": AppWatcher.get().watched()}


@router.post("/unwatch")
async def unwatch(req: WatchRequest, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    result = await asyncio.to_thread(AppWatcher.get().unwatch, req.app)
    return {"result": result, "watched": AppWatcher.get().watched()}


def register_sink(
    loop: asyncio.AbstractEventLoop,
    broadcast: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    """Bridge watcher poll thread → sidecar SSE subscribers."""

    def _sink(event: dict[str, Any]) -> None:
        payloads: list[dict[str, Any]] = [{"type": "app_event", **event}]
        # A proactive trigger fired (Phase 10). Default = SUGGEST (speak it);
        # auto-run only when app_watcher.auto_run_triggers is enabled AND the
        # trigger opted in — auto-running actions from screen changes is exactly
        # what Phase 9's safety guards are for, so it's off by default.
        if event.get("kind") == "app_trigger" and event.get("goal"):
            goal = str(event["goal"])
            app = event.get("app", "an app")
            auto_ok = False
            try:
                from aether.core.config import load_config
                aw = load_config(validate=False).get("app_watcher") or {}
                auto_ok = bool(aw.get("auto_run_triggers")) and bool(event.get("auto"))
            except Exception:  # noqa: BLE001
                auto_ok = False
            if auto_ok:
                payloads.append({"type": "run_request", "goal": goal, "source": "app_trigger"})
                payloads.append({"type": "say", "text": f"{app} changed — running: {goal}"})
            else:
                payloads.append({"type": "say",
                                 "text": f"{app} changed. Want me to {goal}?"})

        def _put() -> None:
            for payload in payloads:
                asyncio.ensure_future(broadcast(payload))

        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:
            pass  # loop shut down

    AppWatcher.get().set_event_sink(_sink)
