"""Async bridge for ask_user: the agent asks, the app answers (POST /answer).

Same shape as confirmation.py: a ``question`` event goes to SSE clients and
the run waits until the app posts the answer, the user skips, or it times
out. With no app connected (no broadcaster) the question returns None at
once and the agent continues on its own judgment.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Awaitable, Callable

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]

_broadcaster: BroadcastFn | None = None
_pending: dict[str, asyncio.Future[str | None]] = {}
DEFAULT_TIMEOUT_SEC = 300.0


def set_broadcaster(fn: BroadcastFn | None) -> None:
    global _broadcaster
    _broadcaster = fn


async def request_answer(question: str, options: list[str] | None = None, *,
                         run_id: str = "", timeout_sec: float = DEFAULT_TIMEOUT_SEC,
                         broadcaster: BroadcastFn | None = None) -> str | None:
    """Ask the connected app; the answer text, or None on skip/timeout."""
    send = broadcaster or _broadcaster
    if send is None:
        return None
    request_id = uuid.uuid4().hex[:12]
    future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
    _pending[request_id] = future
    try:
        # Inside the try, so a send that fails still clears the pending entry.
        await send({"type": "question", "request_id": request_id, "question": question,
                    "options": list(options or []), "run_id": run_id})
        return await asyncio.wait_for(future, timeout=timeout_sec)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        return None
    finally:
        _pending.pop(request_id, None)


def resolve_answer(request_id: str, answer: str | None) -> bool:
    """Deliver an answer (None = skipped). False when the request is unknown."""
    future = _pending.pop(request_id, None)
    if future is None:
        return False
    if not future.done():
        # POST /answer runs on the same event loop as the waiting run.
        future.set_result((answer or "").strip() or None)
    return True


def pending_count() -> int:
    return len(_pending)
