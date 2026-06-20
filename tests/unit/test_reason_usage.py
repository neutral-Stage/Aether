"""Unit test: _reason_step records token usage (Phase 1)."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from aether.core.llm import LLMResponse


@dataclass
class _UsageClient:
    def step(self, system, messages, tools, *, abort_event=None) -> LLMResponse:  # noqa: ANN001
        return LLMResponse(
            text="done", tool_calls=[], raw_content=[], stop_reason="end_turn",
            backend="anthropic", input_tokens=100, output_tokens=50,
        )


@pytest.mark.unit
def test_record_usage_for_response_helper() -> None:
    from aether.core.metrics import MetricsCollector
    from aether.core.orchestrator import record_usage_for_response

    m = MetricsCollector()
    resp = _UsageClient().step("s", [], [])
    record_usage_for_response(m, resp)
    snap = m.snapshot()
    assert snap["provider_costs"]["anthropic"]["tokens_in"] == 100
    assert snap["provider_costs"]["anthropic"]["tokens_out"] == 50
