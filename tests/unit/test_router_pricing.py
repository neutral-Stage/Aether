"""Regression guard: default role providers must be in PROVIDER_PRICES (Phase 1).

Ensures a future config edit cannot silently zero-out the cost dashboard by
pointing a role at an unpriced provider.
"""
from __future__ import annotations

import pytest

from aether.core.metrics import PROVIDER_PRICES
from aether.core.router import RouterConfig


@pytest.mark.unit
def test_default_role_providers_are_priced():
    cfg = RouterConfig.load()
    roles = cfg.raw.get("roles", {})
    providers = cfg.raw.get("providers", {})
    for role in ("cloud_frontier", "vision"):
        prov = roles.get(role, {}).get("provider")
        assert prov is not None, f"{role} has no provider"
        label = providers.get(prov, {}).get("provider_label", prov)
        assert label in PROVIDER_PRICES, (
            f"{role} provider '{prov}' (label '{label}') is not in PROVIDER_PRICES — "
            f"cost dashboard would show $0 for it"
        )
