"""aether.core.providers.is_loopback_url: only a server on this Mac counts."""
from __future__ import annotations

import pytest

from aether.core.providers import is_loopback_url


@pytest.mark.parametrize("url", [
    "http://localhost:11434/v1",
    "http://LOCALHOST:8080",
    "http://127.0.0.1:8080/v1",
    "https://127.1.2.3/v1",
    "http://[::1]:8080/v1",
])
def test_loopback(url: str) -> None:
    assert is_loopback_url(url)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1.attacker.example/v1",
    "http://127.attacker.example/v1",
    "http://localhost.attacker.example/v1",
    "http://10.0.0.5:8080/v1",
    "https://api.z.ai/api/paas/v4",
    "http://[::2]:8080",
    "not a url",
    "",
])
def test_not_loopback(url: str) -> None:
    assert not is_loopback_url(url)


def test_env_override_switches_role_provider_and_has_cloud_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    from aether.core.config import ROOT
    from aether.core.providers import has_cloud_backend, merge_role_config
    from aether.core.router import RouterConfig

    raw = RouterConfig.load(ROOT / "configs" / "router.yaml").raw
    assert not has_cloud_backend(raw, api_keys={})

    monkeypatch.setenv("AETHER_CLOUD_FRONTIER_PROVIDER", "ollama_local")
    monkeypatch.setenv("AETHER_VISION_PROVIDER", "ollama_local")
    merged = merge_role_config(raw, "cloud_frontier")
    assert merged["model"] == "qwen3.8:27b-mlx"
    assert merged["base_url"] == "http://localhost:11434/v1"
    assert has_cloud_backend(raw, api_keys={})

    from aether.core.router import Router, RouterConfig as RC, RouteTier

    router = Router(router_cfg=RC(raw=raw), api_keys={})
    client = router.pick_client_with_failover(RouteTier.CLOUD_FRONTIER)
    assert getattr(client, "model", None) == "qwen3.8:27b-mlx"
