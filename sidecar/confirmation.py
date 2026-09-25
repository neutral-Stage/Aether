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
# Each pending request resolves to (approved, the user's edits to a draft).
_pending: dict[str, tuple[str, asyncio.Future[tuple[bool, dict[str, str]]]]] = {}


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
    approved, _edits = await request_draft_confirmation(
        description, None, run_id=run_id, tool=tool, timeout_sec=timeout_sec)
    return approved


async def request_draft_confirmation(
    description: str,
    draft: list[dict] | None,
    *,
    run_id: str = "",
    tool: str = "",
    timeout_sec: float = 300.0,
) -> tuple[bool, dict[str, str]]:
    """Confirmation that may carry an editable draft; returns (approved, edits)."""
    if _broadcaster is None:
        return False, {}

    request_id = uuid.uuid4().hex[:12]
    loop = asyncio.get_running_loop()
    future: asyncio.Future[tuple[bool, dict[str, str]]] = loop.create_future()
    _pending[request_id] = (description, future)

    event = {
        "type": "confirm_request",
        "request_id": request_id,
        "description": description,
        "tool": tool,
        "run_id": run_id,
    }
    if draft:
        event["draft"] = draft
    await _broadcaster(event)

    try:
        return await asyncio.wait_for(future, timeout=timeout_sec)
    except asyncio.TimeoutError:
        _pending.pop(request_id, None)
        return False, {}


def resolve_confirmation(request_id: str, approved: bool,
                         edits: dict[str, str] | None = None) -> bool:
    """Resolve a pending confirmation. Returns True if request_id was valid."""
    entry = _pending.pop(request_id, None)
    if entry is None:
        return False
    _desc, future = entry
    if not future.done():
        clean = {str(k): str(v) for k, v in (edits or {}).items()} if approved else {}
        future.set_result((approved, clean))
    return True


def pending_count() -> int:
    return len(_pending)
