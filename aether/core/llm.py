"""LLM clients — Anthropic cloud, local HTTP (Ollama/MLX), and vision.

Kept provider-isolated so the router can swap backends per step.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)


def _check_abort(abort_event: threading.Event | None) -> None:
    if abort_event is not None and abort_event.is_set():
        from .stop import StopRequested
        raise StopRequested("STOP requested — LLM call cancelled.")


def _http_client_for_llm(abort_event: threading.Event | None):
    """Create an httpx client registered for STOP cancellation."""
    import httpx
    from . import stop as stop_ctl

    client = httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0))

    def _close() -> None:
        try:
            client.close()
        except Exception:
            pass

    if abort_event is not None:
        stop_ctl.register_http_closer(_close)
    return client, _close


@dataclass
class LLMResponse:
    text: str                       # any assistant text in this turn
    tool_calls: list[dict]          # [{id, name, input}]
    raw_content: list               # original content blocks (to echo back)
    stop_reason: str | None
    backend: str = "anthropic"


class LLMBackend(Protocol):
    def step(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        *,
        abort_event: threading.Event | None = None,
    ) -> LLMResponse: ...


class LLM:
    def __init__(self, api_key: str, model: str, max_tokens: int = 1024,
                 temperature: float = 0.0):
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set (see .env.example).")
        self._api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    def step(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        *,
        abort_event: threading.Event | None = None,
    ) -> LLMResponse:
        """One model turn. `messages` is the running Anthropic message list."""
        _check_abort(abort_event)
        import anthropic
        from . import stop as stop_ctl

        http_client, closer = _http_client_for_llm(abort_event)
        try:
            client = anthropic.Anthropic(api_key=self._api_key, http_client=http_client)
            _check_abort(abort_event)
            resp = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=system,
                tools=tools,
                messages=messages,
            )
        finally:
            stop_ctl.unregister_http_closer(closer)
            try:
                http_client.close()
            except Exception:
                pass
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": dict(block.input or {}),
                })
        return LLMResponse(
            text="\n".join(text_parts).strip(),
            tool_calls=tool_calls,
            raw_content=resp.content,
            stop_reason=resp.stop_reason,
            backend="anthropic",
        )

    def analyze_image(self, image_path: str, prompt: str) -> str:
        """Send a screenshot to Claude vision."""
        import base64
        from pathlib import Path as P

        media_type = "image/png"
        p = P(image_path)
        if p.suffix.lower() in (".jpg", ".jpeg"):
            media_type = "image/jpeg"
        data = base64.standard_b64encode(p.read_bytes()).decode("ascii")
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": media_type, "data": data,
                    }},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        return "\n".join(parts).strip()

    @staticmethod
    def assistant_turn(content: Any) -> dict:
        return {"role": "assistant", "content": content}

    @staticmethod
    def tool_results_turn(results: list[dict]) -> dict:
        """results: [{tool_use_id, content}] -> a user turn with tool_result blocks."""
        return assistant_tool_results_turn(results)


def assistant_tool_results_turn(results: list[dict]) -> dict:
    """Anthropic-format tool results turn (shared across backends)."""
    blocks = [{
        "type": "tool_result",
        "tool_use_id": r["tool_use_id"],
        "content": r["content"],
    } for r in results]
    return {"role": "user", "content": blocks}


class OpenAICompatibleClient:
    """OpenAI Chat Completions API (OpenAI, OpenRouter, Groq, Fireworks, Kilo, Kie, Z.ai, …)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        max_tokens: int = 1024,
        temperature: float = 0.0,
        backend_name: str = "openai_compatible",
        extra_headers: dict[str, str] | None = None,
    ):
        if not api_key:
            raise RuntimeError("API key is not set for OpenAI-compatible provider.")
        from openai import OpenAI

        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.backend = backend_name
        headers = extra_headers or {}
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._extra_headers = headers
        self._client = OpenAI(api_key=api_key, base_url=self._base_url, default_headers=headers)

    def step(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        *,
        abort_event: threading.Event | None = None,
    ) -> LLMResponse:
        _check_abort(abort_event)
        from openai import OpenAI
        from . import stop as stop_ctl

        http_client, closer = _http_client_for_llm(abort_event)
        try:
            client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                default_headers=self._extra_headers,
                http_client=http_client,
            )
            oai_messages = _anthropic_messages_to_openai(system, messages)
            oai_tools = _anthropic_tools_to_openai(tools) if tools else None
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": oai_messages,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
            }
            if oai_tools:
                kwargs["tools"] = oai_tools
                kwargs["tool_choice"] = "auto"
            _check_abort(abort_event)
            resp = client.chat.completions.create(**kwargs)
        finally:
            stop_ctl.unregister_http_closer(closer)
            try:
                http_client.close()
            except Exception:
                pass
        choice = resp.choices[0]
        text = choice.message.content or ""
        tool_calls: list[dict] = []
        raw_blocks: list[dict] = []
        if text:
            raw_blocks.append({"type": "text", "text": text})
        for tc in choice.message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            call = {"id": tc.id, "name": tc.function.name, "input": args}
            tool_calls.append(call)
            raw_blocks.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.function.name,
                "input": args,
            })
        stop = choice.finish_reason or "end_turn"
        if tool_calls and stop == "tool_calls":
            stop = "tool_use"
        return LLMResponse(
            text=text if not tool_calls else "",
            tool_calls=tool_calls,
            raw_content=raw_blocks,
            stop_reason=stop,
            backend=self.backend,
        )

    def analyze_image(self, image_path: str, prompt: str) -> str:
        import base64
        from pathlib import Path as P

        media_type = "image/png"
        p = P(image_path)
        if p.suffix.lower() in (".jpg", ".jpeg"):
            media_type = "image/jpeg"
        data = base64.standard_b64encode(p.read_bytes()).decode("ascii")
        resp = self._client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{media_type};base64,{data}",
                    }},
                ],
            }],
        )
        return (resp.choices[0].message.content or "").strip()


class GoogleGeminiClient(OpenAICompatibleClient):
    """Gemini via Google's OpenAI-compatible endpoint (no extra SDK required)."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ):
        super().__init__(
            api_key=api_key,
            model=model,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            max_tokens=max_tokens,
            temperature=temperature,
            backend_name="google",
        )


class LocalHTTPClient:
    """Ollama / llama.cpp / MLX HTTP chat API with tool-use parsing.

    Expects OpenAI-style or Ollama /api/chat responses. Tool calls are parsed
    from JSON blocks in the model text when native tool_use is unavailable.
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:11434/api/chat",
        model: str = "llama3.2:3b",
        max_tokens: int = 512,
        temperature: float = 0.0,
        timeout: float = 30.0,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self._available: bool | None = None

    def available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import urllib.request
            # Ollama tags endpoint for health check
            base = self.endpoint.split("/api/")[0]
            req = urllib.request.Request(f"{base}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                self._available = resp.status == 200
        except Exception:
            self._available = False
        return self._available

    def step(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        *,
        abort_event: threading.Event | None = None,
    ) -> LLMResponse:
        _check_abort(abort_event)
        import urllib.request

        ollama_messages = [{"role": "system", "content": system}]
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content")
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "tool_result":
                            text_parts.append(f"[tool result]: {block.get('content', '')}")
                        elif block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                    else:
                        text_parts.append(str(block))
                ollama_messages.append({"role": role, "content": "\n".join(text_parts)})
            else:
                ollama_messages.append({"role": role, "content": str(content)})

        tool_hint = _format_tools_for_local(tools)
        if tool_hint:
            ollama_messages[0]["content"] += "\n\n" + tool_hint

        payload = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": False,
            "options": {"temperature": self.temperature, "num_predict": self.max_tokens},
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            _check_abort(abort_event)
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            log.warning("Local model call failed: %s", e)
            raise RuntimeError(f"Local model unavailable: {e}") from e

        text = ""
        if "message" in body:
            text = body["message"].get("content", "") or ""
        elif "choices" in body:
            text = body["choices"][0]["message"].get("content", "") or ""

        tool_calls = _parse_tool_calls_from_text(text)
        raw = [{"type": "text", "text": text}] if text else []
        return LLMResponse(
            text=text if not tool_calls else "",
            tool_calls=tool_calls,
            raw_content=raw,
            stop_reason="tool_use" if tool_calls else "end_turn",
            backend="local_http",
        )


class VisionLLM:
    """Vision path: OCR first, optional cloud VLM via the frontier client."""

    def __init__(
        self,
        cloud_llm: Any,
        ocr_only: bool = False,
        vlm_endpoint: str | None = None,
        vlm_model: str | None = None,
    ):
        self.cloud = cloud_llm
        self.ocr_only = ocr_only
        self.vlm_endpoint = vlm_endpoint
        self.vlm_model = vlm_model
        self._last_vision_context: str = ""

    @property
    def last_vision_context(self) -> str:
        return self._last_vision_context

    def step(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        *,
        abort_event: threading.Event | None = None,
    ) -> LLMResponse:
        # Vision tier still uses cloud for reasoning; OCR context is injected upstream
        return self.cloud.step(system, messages, tools, abort_event=abort_event)

    def analyze_screenshot(self, image_path: str, prompt: str | None = None) -> str:
        from ..perception import ocr as ocr_mod

        ocr_text = ocr_mod.recognize_text_formatted(image_path)
        parts = [f"Screenshot: {image_path}", ocr_text]

        if self.ocr_only:
            self._last_vision_context = "\n".join(parts)
            return self._last_vision_context

        if self.vlm_endpoint and self.vlm_model:
            try:
                vlm = self._call_vlm_http(image_path, prompt or "Describe the UI.")
                parts.append(f"VLM analysis:\n{vlm}")
            except Exception as e:
                parts.append(f"VLM error: {e}")
        else:
            try:
                analyze = getattr(self.cloud, "analyze_image", None)
                if callable(analyze):
                    analysis = analyze(
                        image_path,
                        prompt or ("Describe visible UI elements, buttons, and text. "
                                   "Be concise and actionable."),
                    )
                else:
                    analysis = ""
                if analysis:
                    parts.append(f"Vision analysis:\n{analysis}")
            except Exception as e:
                parts.append(f"Cloud vision error: {e}")

        self._last_vision_context = "\n".join(parts)
        return self._last_vision_context

    def _call_vlm_http(self, image_path: str, prompt: str) -> str:
        import base64
        import urllib.request

        data_b64 = base64.standard_b64encode(Path(image_path).read_bytes()).decode()
        payload = {
            "model": self.vlm_model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data_b64}"}},
                ],
            }],
            "max_tokens": 1024,
        }
        endpoint = self.vlm_endpoint.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint = endpoint + "/v1/chat/completions"
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode())
        return body["choices"][0]["message"]["content"]


def _format_tools_for_local(tools: list[dict]) -> str:
    if not tools:
        return ""
    lines = ["Available tools — respond with a JSON object ONLY when calling a tool:",
             '{"tool": "<name>", "input": {...}}']
    for t in tools[:20]:
        lines.append(f"- {t.get('name')}: {t.get('description', '')[:120]}")
    return "\n".join(lines)


def _parse_tool_calls_from_text(text: str) -> list[dict]:
    """Extract tool call JSON from local model free text."""
    tool_calls: list[dict] = []
    # Try fenced JSON
    for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
        tc = _json_to_tool_call(match.group(1))
        if tc:
            tool_calls.append(tc)
    if tool_calls:
        return tool_calls
    # Bare JSON object with "tool" key
    for match in re.finditer(r"\{[^{}]*\"tool\"\s*:\s*\"[^\"]+\"[^{}]*\}", text):
        tc = _json_to_tool_call(match.group(0))
        if tc:
            tool_calls.append(tc)
    return tool_calls


def _json_to_tool_call(raw: str) -> dict | None:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    name = obj.get("tool") or obj.get("name")
    if not name:
        return None
    return {
        "id": f"local_{uuid.uuid4().hex[:12]}",
        "name": name,
        "input": dict(obj.get("input") or obj.get("arguments") or {}),
    }


def _anthropic_tools_to_openai(tools: list[dict]) -> list[dict]:
    oai_tools: list[dict] = []
    for tool in tools:
        oai_tools.append({
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            },
        })
    return oai_tools


def _anthropic_messages_to_openai(system: str, messages: list[dict]) -> list[dict]:
    oai: list[dict] = []
    if system:
        oai.append({"role": "system", "content": system})
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content")
        if role == "assistant":
            if isinstance(content, list):
                text_parts: list[str] = []
                tool_calls: list[dict] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(str(block.get("text", "")))
                    elif btype == "tool_use":
                        tool_calls.append({
                            "id": block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input") or {}),
                            },
                        })
                assistant: dict[str, Any] = {"role": "assistant"}
                if text_parts:
                    assistant["content"] = "\n".join(text_parts)
                elif tool_calls:
                    assistant["content"] = None
                else:
                    assistant["content"] = ""
                if tool_calls:
                    assistant["tool_calls"] = tool_calls
                oai.append(assistant)
            else:
                oai.append({"role": "assistant", "content": str(content)})
            continue
        if role == "user" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    oai.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": str(block.get("content", "")),
                    })
                elif block.get("type") == "text":
                    oai.append({"role": "user", "content": str(block.get("text", ""))})
            continue
        oai.append({"role": role, "content": str(content)})
    return oai
