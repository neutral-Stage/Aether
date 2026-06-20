"""Streaming HUD bridge: forwards orchestrator HUD updates to the sidecar event queue."""
from __future__ import annotations

import asyncio
import threading
from typing import Any


class StreamingHUD:
    """Drop-in replacement for tkinter HUD; emits dict events for SSE clients."""

    def __init__(self) -> None:
        self.enabled = True
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind(self, queue: asyncio.Queue[dict[str, Any]], loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            self._queue = queue
            self._loop = loop

    def unbind(self) -> None:
        with self._lock:
            self._queue = None
            self._loop = None

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def _emit(self, event: dict[str, Any]) -> None:
        with self._lock:
            q, loop = self._queue, self._loop
        if q is None or loop is None:
            return
        try:
            loop.call_soon_threadsafe(q.put_nowait, event)
        except RuntimeError:
            pass

    def update(self, **kwargs: Any) -> None:
        self._emit({"type": "hud", **kwargs})


class NullTTS:
    """TTS noop — native Swift shell speaks via AVSpeechSynthesizer."""

    def speak(self, text: str) -> None:
        return


def patch_agent_for_sidecar(agent, hud: StreamingHUD, emit_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
    """Route spoken lines to SSE; disable Python TTS; wire async confirmations."""
    from . import confirmation

    hud.bind(emit_queue, loop)
    agent.hud = hud
    agent.tts = NullTTS()
    agent.cfg.raw.setdefault("agent", {})["narrate"] = False

    async def _broadcast(event: dict) -> None:
        loop.call_soon_threadsafe(emit_queue.put_nowait, event)

    confirmation.set_broadcaster(_broadcast)

    async def confirm_hook(description: str) -> bool:
        return await confirmation.request_confirmation(description)

    agent.confirm_async = confirm_hook

    def say_with_event(text: str) -> None:
        loop.call_soon_threadsafe(
            emit_queue.put_nowait,
            {"type": "say", "text": text},
        )
        print(f"\n🔊 Aether: {text}\n")

    agent.say = say_with_event  # type: ignore[method-assign]

    async def say_async(text: str) -> None:
        await asyncio.to_thread(say_with_event, text)

    agent.say_async = say_async  # type: ignore[method-assign]
    return agent
