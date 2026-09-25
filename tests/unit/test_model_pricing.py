"""Cost estimates use the model id carried on each response."""
from __future__ import annotations

import pytest

from aether.core.llm import LLMResponse
from aether.core.metrics import MetricsCollector, estimate_cost_usd
from aether.core.orchestrator import record_usage_for_response


def test_glm_53_flash_list_price() -> None:
    # $0.15 in / $0.50 out per 1M tokens.
    assert estimate_cost_usd("zai", "glm-5.3-flash", 1_000_000, 1_000_000) == pytest.approx(0.65)


def test_model_price_beats_provider_default() -> None:
    assert estimate_cost_usd("anthropic", "claude-opus-5", 1000, 0) == pytest.approx(0.005)
    assert estimate_cost_usd("anthropic", None, 1000, 0) == pytest.approx(0.002)


def test_response_model_reaches_metrics() -> None:
    m = MetricsCollector.get()
    m.start_run("r", "goal")
    resp = LLMResponse(text="", tool_calls=[], raw_content=[], stop_reason="end_turn",
                       backend="anthropic", input_tokens=1000, output_tokens=1000,
                       model="claude-opus-5")
    record_usage_for_response(m, resp)
    expected = 0.005 + 0.025
    assert m.run_cost() == pytest.approx(expected)
    assert m.run_cost("r") == pytest.approx(expected)
    assert m.snapshot()["provider_costs"]["anthropic"]["cost_usd"] == pytest.approx(expected)
    m.end_run("idle")
    assert m.run_cost("r") == pytest.approx(expected)
    assert m.snapshot()["recent_runs"][-1]["cost_usd"] == pytest.approx(expected)
