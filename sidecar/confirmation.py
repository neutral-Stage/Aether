"""Async confirmation bridge for destructive actions (Phase 7, FR-23).

When the native Swift shell is connected via SSE, confirmations are emitted as
``confirm_request`` events and resolved via POST /confirm. Falls back to stdin
when no broadcaster is bound (CLI path).
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Awaitable, Callable

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]

_broadcaster: BroadcastFn | None = None
_pending: dict[str, tuple[str, asyncio.Future[bool]]] = {}


def set_broadcaster(fn: BroadcastFn | None) -> None:
    global _broadcaster
    _broadcaster = fn


async def request_confirmation(
    description: str,
    *,
    run_id: str = "",
    tool: str = "",
    timeout_sec: float = 120.0,
) -> bool:
    """Request user confirmation; returns False on decline or timeout."""
    if _broadcaster is None:
        return False

    request_id = uuid.uuid4().hex[:12]
    loop = asyncio.get_running_loop()
    future: asyncio.Future[bool] = loop.create_future()
    _pending[request_id] = (description, future)

    await _broadcaster({
        "type": "confirm_request",
        "request_id": request_id,
        "description": description,
        "tool": tool,
        "run_id": run_id,
    })

    try:
        return await asyncio.wait_for(future, timeout=timeout_sec)
    except asyncio.TimeoutError:
        _pending.pop(request_id, None)
        return False


def resolve_confirmation(request_id: str, approved: bool) -> bool:
    """Resolve a pending confirmation. Returns True if request_id was valid."""
    entry = _pending.pop(request_id, None)
    if entry is None:
        return False
    _desc, future = entry
    if not future.done():
        future.set_result(approved)
    return True


def pending_count() -> int:
    return len(_pending)
