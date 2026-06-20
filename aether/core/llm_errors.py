"""LLM provider error classification for router failover (Phase 10, NFR-3)."""
from __future__ import annotations

import threading
from typing import Any


def is_transient_llm_error(exc: BaseException) -> bool:
    """True when another provider in the failover chain may succeed."""
    # OpenAI SDK
    try:
        from openai import APIConnectionError, APIStatusError, RateLimitError

        if isinstance(exc, RateLimitError):
            return True
        if isinstance(exc, APIConnectionError):
            return True
        if isinstance(exc, APIStatusError) and exc.status_code >= 500:
            return True
        if isinstance(exc, APIStatusError) and exc.status_code == 429:
            return True
    except ImportError:
        pass

    # Anthropic SDK
    try:
        from anthropic import APIConnectionError as AnthropicConnError
        from anthropic import APIStatusError as AnthropicStatusError
        from anthropic import RateLimitError as AnthropicRateLimit

        if isinstance(exc, AnthropicRateLimit):
            return True
        if isinstance(exc, AnthropicConnError):
            return True
        if isinstance(exc, AnthropicStatusError) and exc.status_code >= 500:
            return True
        if isinstance(exc, AnthropicStatusError) and exc.status_code == 429:
            return True
    except ImportError:
        pass

    msg = str(exc).lower()
    transient_markers = (
        "rate limit",
        "rate_limit",
        "429",
        "500",
        "502",
        "503",
        "504",
        "timeout",
        "timed out",
        "connection error",
        "overloaded",
        "temporarily unavailable",
    )
    return any(marker in msg for marker in transient_markers)


class FailoverLLMClient:
    """Wraps multiple LLM backends; tries each on transient failures."""

    def __init__(self, clients: list[tuple[str, Any]]):
        if not clients:
            raise RuntimeError("FailoverLLMClient requires at least one backend")
        self._clients = clients
        self.backend = clients[0][0]

    def step(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        *,
        abort_event: threading.Event | None = None,
    ) -> Any:
        last_err: BaseException | None = None
        for name, client in self._clients:
            try:
                try:
                    resp = client.step(
                        system, messages, tools, abort_event=abort_event,
                    )
                except TypeError:
                    resp = client.step(system, messages, tools)
                resp.backend = getattr(resp, "backend", name)
                return resp
            except Exception as exc:  # noqa: BLE001
                from .stop import StopRequested
                if isinstance(exc, StopRequested):
                    raise
                if is_transient_llm_error(exc):
                    last_err = exc
                    continue
                raise
        if last_err is not None:
            raise last_err
        raise RuntimeError("All providers in failover chain failed")

    def analyze_image(self, image_path: str, prompt: str) -> str:
        last_err: BaseException | None = None
        for _name, client in self._clients:
            if not hasattr(client, "analyze_image"):
                continue
            try:
                return client.analyze_image(image_path, prompt)
            except Exception as exc:  # noqa: BLE001
                if is_transient_llm_error(exc):
                    last_err = exc
                    continue
                raise
        if last_err is not None:
            raise last_err
        raise RuntimeError("No vision backend available in failover chain")

    def analyze_screenshot(self, image_path: str) -> str:
        if hasattr(self._clients[0][1], "analyze_screenshot"):
            return self._clients[0][1].analyze_screenshot(image_path)
        return self.analyze_image(image_path, "Describe this screenshot for UI automation.")
