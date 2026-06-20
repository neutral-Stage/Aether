"""Unit tests for token/cost accounting (Phase 1)."""
from __future__ import annotations

import pytest

from aether.core.metrics import MetricsCollector, estimate_cost_usd


@pytest.mark.unit
class TestEstimateCost:
    def test_known_provider_model(self) -> None:
        # claude-sonnet-4-6: 0.003 in + 0.015 out per 1K → 1K each = 0.018
        cost = estimate_cost_usd("anthropic", "claude-sonnet-4-6", 1000, 1000)
        assert cost == pytest.approx(0.018, rel=1e-6)

    def test_local_is_free(self) -> None:
        assert estimate_cost_usd("local_http", None, 5000, 5000) == 0.0

    def test_unknown_provider_zero(self) -> None:
        assert estimate_cost_usd("totally_unknown", None, 1000, 1000) == 0.0

    def test_provider_fallback_when_model_unknown(self) -> None:
        # provider known, model unknown → provider fallback price applies (non-zero)
        assert estimate_cost_usd("anthropic", "some-future-model", 1000, 0) > 0.0


@pytest.mark.unit
class TestRecordUsage:
    def test_accumulates_provider_costs(self) -> None:
        m = MetricsCollector()
        m.record_llm_usage("anthropic", 1000, 1000, model="claude-sonnet-4-6")
        snap = m.snapshot()
        pc = snap["provider_costs"]["anthropic"]
        assert pc["tokens_in"] == 1000
        assert pc["tokens_out"] == 1000
        assert pc["calls"] == 1
        assert pc["cost_usd"] == pytest.approx(0.018, rel=1e-6)
        assert snap["total_cost_usd"] == pytest.approx(0.018, rel=1e-6)
        assert snap["counters"]["tokens_in_anthropic"] == 1000

    def test_unknown_provider_counter(self) -> None:
        m = MetricsCollector()
        m.record_llm_usage("weirdprov", 10, 10)
        snap = m.snapshot()
        assert snap["counters"]["unknown_provider_usage"] == 1
        assert snap["total_cost_usd"] == 0.0

    def test_none_tokens_noop(self) -> None:
        m = MetricsCollector()
        m.record_llm_usage("anthropic", None, None, model="claude-sonnet-4-6")
        snap = m.snapshot()
        assert "anthropic" not in snap["provider_costs"]
