"""Unit tests for per-backend token-usage extraction (Phase 1)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from aether.core.llm import (
    LLMResponse,
    _usage_from_anthropic,
    _usage_from_ollama,
    _usage_from_openai,
)


@pytest.mark.unit
class TestUsageExtractors:
    def test_anthropic_usage(self) -> None:
        resp = SimpleNamespace(usage=SimpleNamespace(input_tokens=12, output_tokens=34))
        assert _usage_from_anthropic(resp) == (12, 34)

    def test_anthropic_usage_missing(self) -> None:
        assert _usage_from_anthropic(SimpleNamespace()) == (None, None)

    def test_openai_usage(self) -> None:
        resp = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=5, completion_tokens=7))
        assert _usage_from_openai(resp) == (5, 7)

    def test_openai_usage_none(self) -> None:
        resp = SimpleNamespace(usage=None)
        assert _usage_from_openai(resp) == (None, None)

    def test_ollama_native_counts(self) -> None:
        body = {"prompt_eval_count": 11, "eval_count": 22, "message": {"content": "hi"}}
        assert _usage_from_ollama(body) == (11, 22)

    def test_ollama_openai_style_usage(self) -> None:
        body = {"usage": {"prompt_tokens": 3, "completion_tokens": 4}}
        assert _usage_from_ollama(body) == (3, 4)

    def test_ollama_no_usage(self) -> None:
        assert _usage_from_ollama({"message": {"content": "hi"}}) == (None, None)


@pytest.mark.unit
class TestLLMResponseFields:
    def test_defaults_none(self) -> None:
        r = LLMResponse(text="x", tool_calls=[], raw_content=[], stop_reason="end_turn")
        assert r.input_tokens is None
        assert r.output_tokens is None
        assert r.cost_usd is None
