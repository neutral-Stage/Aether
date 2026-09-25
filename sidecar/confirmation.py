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
# Each pending request resolves to (approved, the user's edits to a draft,
# whether to approve the same thing for the rest of the conversation).
_Answer = tuple[bool, dict[str, str], bool]
_pending: dict[str, tuple[str, asyncio.Future[_Answer]]] = {}


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
    approved, edits, _remember = await _request(description, run_id=run_id, tool=tool,
                                                timeout_sec=timeout_sec, draft=draft)
    return approved, edits


async def request_grant_confirmation(
    description: str,
    grant: str,
    *,
    run_id: str = "",
    tool: str = "",
    timeout_sec: float = 120.0,
) -> tuple[bool, bool]:
    """A rule-of-two confirmation that also offers approving `grant` (what it
    would cover, e.g. "open pages on example.com") for the rest of the
    conversation. Returns (approved, for the conversation)."""
    approved, _edits, remember = await _request(description, run_id=run_id, tool=tool,
                                                timeout_sec=timeout_sec, grant=grant)
    return approved, bool(approved and remember)


async def _request(description: str, *, run_id: str, tool: str, timeout_sec: float,
                   draft: list[dict] | None = None, grant: str = "") -> _Answer:
    if _broadcaster is None:
        return False, {}, False

    request_id = uuid.uuid4().hex[:12]
    loop = asyncio.get_running_loop()
    future: asyncio.Future[_Answer] = loop.create_future()
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
    if grant:
        event["grant"] = grant
    try:
        # Inside the try, so a send that fails (or a cancelled run) still
        # clears the pending entry.
        await _broadcaster(event)
        return await asyncio.wait_for(future, timeout=timeout_sec)
    except asyncio.TimeoutError:
        return False, {}, False
    finally:
        _pending.pop(request_id, None)


def resolve_confirmation(request_id: str, approved: bool,
                         edits: dict[str, str] | None = None,
                         remember: bool = False) -> bool:
    """Resolve a pending confirmation. Returns True if request_id was valid."""
    entry = _pending.pop(request_id, None)
    if entry is None:
        return False
    _desc, future = entry
    if not future.done():
        clean = {str(k): str(v) for k, v in (edits or {}).items()} if approved else {}
        future.set_result((approved, clean, bool(approved and remember)))
    return True


def pending_count() -> int:
    return len(_pending)
