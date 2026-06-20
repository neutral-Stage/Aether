"""OpenAI Realtime API WebSocket client (Phase 10 spike → product).

Bridges bidirectional audio/events for low-latency voice when
`voice.mode: realtime` and `beta.realtime_voice: true`.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "gpt-4o-realtime-preview-2024-12-17"
_REALTIME_URL = "wss://api.openai.com/v1/realtime"


@dataclass
class RealtimeConfig:
    api_key: str
    model: str = _DEFAULT_MODEL
    voice: str = "alloy"
    instructions: str = (
        "You are Aether, a helpful macOS AI assistant. "
        "Respond concisely in spoken English."
    )

    @classmethod
    def from_env(cls, *, model: str | None = None, voice: str = "alloy") -> "RealtimeConfig | None":
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            return None
        return cls(api_key=key, model=model or _DEFAULT_MODEL, voice=voice)


@dataclass
class RealtimeSession:
    """Manages one OpenAI Realtime WebSocket session."""

    config: RealtimeConfig
    _ws: Any = field(default=None, repr=False)
    _recv_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _event_handlers: list[Callable[[dict[str, Any]], None]] = field(default_factory=list)
    _closed: bool = False

    async def connect(self) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError(
                "websockets package required for realtime voice: pip install websockets"
            ) from exc

        url = f"{_REALTIME_URL}?model={self.config.model}"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
        self._ws = await websockets.connect(url, additional_headers=headers)
        await self._send({
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "instructions": self.config.instructions,
                "voice": self.config.voice,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "turn_detection": {"type": "server_vad"},
            },
        })
        self._recv_task = asyncio.create_task(self._recv_loop())
        log.info("Realtime session connected (model=%s)", self.config.model)

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                for handler in list(self._event_handlers):
                    try:
                        handler(event)
                    except Exception as exc:  # noqa: BLE001
                        log.debug("Realtime handler error: %s", exc)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            log.warning("Realtime recv loop ended: %s", exc)

    def on_event(self, handler: Callable[[dict[str, Any]], None]) -> None:
        self._event_handlers.append(handler)

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("Realtime session not connected")
        await self._ws.send(json.dumps(payload))

    async def send_audio_chunk(self, pcm16_bytes: bytes) -> None:
        """Append PCM16 audio to the input buffer."""
        if not pcm16_bytes:
            return
        b64 = base64.standard_b64encode(pcm16_bytes).decode("ascii")
        await self._send({"type": "input_audio_buffer.append", "audio": b64})

    async def commit_audio(self) -> None:
        await self._send({"type": "input_audio_buffer.commit"})

    async def request_response(self) -> None:
        await self._send({"type": "response.create"})

    async def send_text(self, text: str) -> None:
        await self._send({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
        })
        await self.request_response()

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Async iterator over session events (for sidecar bridge)."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        def _put(ev: dict[str, Any]) -> None:
            try:
                queue.put_nowait(ev)
            except asyncio.QueueFull:
                pass

        self.on_event(_put)
        while not self._closed:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield ev
            except asyncio.TimeoutError:
                yield {"type": "ping"}

    async def close(self) -> None:
        self._closed = True
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self._ws is not None:
            await self._ws.close()
        self._ws = None
