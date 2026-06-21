"""Unit tests for native Ollama tool-calling (Phase 1)."""
from __future__ import annotations

import pytest

from aether.core.llm import LocalHTTPClient, _parse_ollama_tool_calls


@pytest.mark.unit
class TestParseOllamaToolCalls:
    def test_parses_dict_arguments(self) -> None:
        body = {"message": {"tool_calls": [
            {"function": {"name": "open_app", "arguments": {"name": "Finder"}}}
        ]}}
        calls = _parse_ollama_tool_calls(body)
        assert len(calls) == 1
        assert calls[0]["name"] == "open_app"
        assert calls[0]["input"] == {"name": "Finder"}
        assert calls[0]["id"].startswith("local_")

    def test_parses_string_arguments(self) -> None:
        body = {"message": {"tool_calls": [
            {"function": {"name": "click", "arguments": "{\"element_index\": 3}"}}
        ]}}
        calls = _parse_ollama_tool_calls(body)
        assert calls[0]["input"] == {"element_index": 3}

    def test_no_tool_calls(self) -> None:
        assert _parse_ollama_tool_calls({"message": {"content": "hello"}}) == []

    def test_native_tools_flag_defaults_false(self) -> None:
        c = LocalHTTPClient()
        assert c.native_tools is False
