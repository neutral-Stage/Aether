"""Integration: /dashboard renders the estimated cost panel (Phase 1)."""
from __future__ import annotations

import pytest


@pytest.mark.integration
def test_dashboard_has_cost_panel(sidecar_client) -> None:  # noqa: ANN001
    from aether.core.metrics import MetricsCollector

    MetricsCollector.get().record_llm_usage("anthropic", 1000, 500, model="claude-sonnet-4-6")
    resp = sidecar_client.get("/dashboard")
    assert resp.status_code == 200
    body = resp.text
    assert "Cost &amp; tokens" in body or "Cost & tokens" in body
    assert "anthropic" in body
