"""OpenAI Realtime API client: speech in, speech out, with two tools.

Used when ``voice.mode: realtime`` and ``beta.realtime_voice: true`` (needs
an OpenAI key). The app streams the microphone only while push-to-talk is
held and commits the turn on release: nothing is uploaded continuously.

- ``look_at_screen`` attaches a screenshot as an image message before the
  tool's output, so the model answers about what is on screen.
- ``do_task`` hands a request to Aether's agent (same policy gate and
  confirmations as any run) and returns its result.
- ``interrupt`` stops the current reply and truncates it at the audio the
  user actually heard, so the conversation matches what was said.

Speaks the GA Realtime API (session.type "realtime", output_audio events);
events from the older beta names are normalized for the app.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-realtime"
DEFAULT_VOICE = "marin"
REALTIME_URL = "wss://api.openai.com/v1/realtime"
SAMPLE_RATE = 24_000

TOOLS: list[dict[str, Any]] = [
    {"type": "function", "name": "look_at_screen",
     "description": ("See what is on the user's screen right now. Use before answering "
                     "anything about 'this', 'here' or what they are looking at."),
     "parameters": {"type": "object", "properties": {}}},
    {"type": "function", "name": "do_task",
     "description": ("Have Aether do something on the Mac (open, find, write, change, send). "
                     "Say briefly what you are doing first; the result comes back as text."),
     "parameters": {"type": "object", "properties": {
         "goal": {"type": "string", "description": "the request, in the user's words"}},
         "required": ["goal"]}},
]

# Beta event names → GA names (what the app handles).
_EVENT_ALIASES = {
    "response.audio.delta": "response.output_audio.delta",
    "response.audio.done": "response.output_audio.done",
    "response.audio_transcript.delta": "response.output_audio_transcript.delta",
    "response.audio_transcript.done": "response.output_audio_transcript.done",
    "response.text.delta": "response.output_text.delta",
}

ScreenFn = Callable[[], Awaitable[str | None]]      # → PNG path
TaskFn = Callable[[str], Awaitable[str]]            # goal → result text


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    kind = event.get("type")
    if kind in _EVENT_ALIASES:
        return {**event, "type": _EVENT_ALIASES[kind]}
    return event


@dataclass
class RealtimeConfig:
    api_key: str
    model: str = DEFAULT_MODEL
    voice: str = DEFAULT_VOICE
    instructions: str = (
        "You are Aether, a helpful assistant on the user's Mac. Answer briefly and "
        "naturally, in spoken English. Use look_at_screen for questions about what they "
        "see, and do_task to act on the Mac.")

    @classmethod
    def from_env(cls, *, model: str | None = None, voice: str | None = None
                 ) -> RealtimeConfig | None:
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            return None
        return cls(api_key=key, model=model or DEFAULT_MODEL, voice=voice or DEFAULT_VOICE)

    def session_update(self) -> dict[str, Any]:
        pcm = {"type": "audio/pcm", "rate": SAMPLE_RATE}
        return {"type": "session.update", "session": {
            "type": "realtime",
            "instructions": self.instructions,
            "output_modalities": ["audio"],
            "audio": {
                # Push-to-talk: the app commits each turn, so no server VAD.
                "input": {"format": pcm, "turn_detection": None},
                "output": {"format": pcm, "voice": self.voice},
            },
            "tools": TOOLS,
            "tool_choice": "auto",
        }}


@dataclass
class RealtimeSession:
    """One OpenAI Realtime WebSocket session."""

    config: RealtimeConfig
    screen_fn: ScreenFn | None = None
    task_fn: TaskFn | None = None
    _ws: Any = field(default=None, repr=False)
    _recv_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _event_handlers: list[Callable[[dict[str, Any]], None]] = field(default_factory=list)
    _tool_tasks: set[asyncio.Task[None]] = field(default_factory=set, repr=False)
    _closed: bool = False

    async def connect(self, ws: Any = None) -> None:
        if ws is None:
            try:
                import websockets
            except ImportError as exc:
                raise RuntimeError(
                    "websockets package required for realtime voice: pip install websockets"
                ) from exc
            url = f"{REALTIME_URL}?model={self.config.model}"
            ws = await websockets.connect(
                url, additional_headers={"Authorization": f"Bearer {self.config.api_key}"})
        self._ws = ws
        await self._send(self.config.session_update())
        self._recv_task = asyncio.create_task(self._recv_loop())
        log.info("Realtime session connected (model=%s)", self.config.model)

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    event = normalize_event(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    continue
                if event.get("type") == "response.function_call_arguments.done":
                    task = asyncio.create_task(self.handle_tool_call(event))
                    self._tool_tasks.add(task)
                    task.add_done_callback(self._tool_tasks.discard)
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

    # ---- input --------------------------------------------------------------------------

    async def send_audio_chunk(self, pcm16_bytes: bytes) -> None:
        """Append 24 kHz mono PCM16 audio to the input buffer."""
        if not pcm16_bytes:
            return
        b64 = base64.standard_b64encode(pcm16_bytes).decode("ascii")
        await self._send({"type": "input_audio_buffer.append", "audio": b64})

    async def commit_audio(self) -> None:
        """End of the user's turn (push-to-talk released): answer it."""
        await self._send({"type": "input_audio_buffer.commit"})
        await self.request_response()

    async def clear_audio(self) -> None:
        await self._send({"type": "input_audio_buffer.clear"})

    async def request_response(self) -> None:
        await self._send({"type": "response.create"})

    async def send_text(self, text: str) -> None:
        await self._send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": text}]}})
        await self.request_response()

    async def interrupt(self, item_id: str | None, audio_end_ms: int) -> None:
        """The user started talking over the reply: stop it where they stopped hearing it."""
        await self._send({"type": "response.cancel"})
        if item_id:
            await self._send({"type": "conversation.item.truncate", "item_id": item_id,
                              "content_index": 0, "audio_end_ms": max(0, int(audio_end_ms))})

    # ---- tools ---------------------------------------------------------------------------

    async def handle_tool_call(self, event: dict[str, Any]) -> None:
        call_id = str(event.get("call_id") or "")
        name = str(event.get("name") or "")
        try:
            args = json.loads(event.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError):
            args = {}
        if not isinstance(args, dict):
            args = {}
        try:
            if name == "look_at_screen":
                output = await self._look_at_screen()
            elif name == "do_task":
                goal = " ".join(str(args.get("goal") or "").split())
                if not goal:
                    output = "No goal was given."
                elif self.task_fn is None:
                    output = "Doing tasks is not available in this session."
                else:
                    output = (await self.task_fn(goal))[:4000] or "Done."
            else:
                output = f"Unknown tool {name}."
        except Exception as exc:  # noqa: BLE001 — the model hears the failure
            output = f"That failed: {str(exc)[:300]}"
        await self._send({"type": "conversation.item.create", "item": {
            "type": "function_call_output", "call_id": call_id, "output": output}})
        await self.request_response()

    async def _look_at_screen(self) -> str:
        if self.screen_fn is None:
            return "Seeing the screen is not available in this session."
        path = await self.screen_fn()
        if not path:
            return "The screen could not be captured (is Screen Recording allowed?)."
        data = base64.standard_b64encode(Path(path).read_bytes()).decode("ascii")
        # The image goes in BEFORE the tool output, as a user message the model can see.
        await self._send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user", "content": [
                {"type": "input_image", "image_url": f"data:image/png;base64,{data}"}]}})
        return "The current screen is attached above."

    # ---- plumbing ------------------------------------------------------------------------

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Async iterator over session events (for the sidecar bridge)."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.on_event(queue.put_nowait)
        while not self._closed:
            try:
                yield await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                yield {"type": "ping"}

    async def close(self) -> None:
        self._closed = True
        for task in list(self._tool_tasks):
            task.cancel()
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self._ws is not None:
            await self._ws.close()
        self._ws = None
