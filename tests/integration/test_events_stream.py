"""Subscribe-only /events stream carries proactive run_request (Phase 11).

Drives the async generator directly (not through TestClient) — the /events
stream is infinite, so an HTTP-level test would hang.
"""
from __future__ import annotations

import asyncio
import json

import pytest


@pytest.mark.integration
def test_events_stream_delivers_broadcast():
    import sidecar.server as server

    async def drive() -> str:
        agen = server._events_stream()
        # start the generator so it subscribes its queue, then push a broadcast
        first = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0.05)
        await server._broadcast({"type": "run_request", "goal": "run the tests"})
        chunk = await asyncio.wait_for(first, timeout=2.0)
        await agen.aclose()
        return chunk

    chunk = asyncio.run(drive())
    assert chunk.startswith("data:")
    evt = json.loads(chunk[len("data:"):].strip())
    assert evt["type"] == "run_request"
    assert evt["goal"] == "run the tests"


@pytest.mark.integration
def test_events_stream_breaks_when_evicted(monkeypatch):
    # if _broadcast evicted our queue (QueueFull), the stream must END so the
    # client reconnects — not ping forever on a dead queue
    import sidecar.server as server

    monkeypatch.setattr(server, "_EVENTS_PING_SEC", 0.05)  # don't wait 30s

    async def drive() -> bool:
        agen = server._events_stream()
        first = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0.02)                 # let it subscribe
        async with server._subscribers_lock:
            server._event_subscribers.clear()     # simulate QueueFull eviction
        try:
            await asyncio.wait_for(first, timeout=1.0)
            return False                          # yielded a ping instead of ending
        except StopAsyncIteration:
            return True                           # ended → client will reconnect
        finally:
            await agen.aclose()

    assert asyncio.run(drive()) is True


@pytest.mark.integration
def test_events_endpoint_registered():
    from sidecar.server import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/events" in paths
