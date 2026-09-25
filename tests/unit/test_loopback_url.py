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
