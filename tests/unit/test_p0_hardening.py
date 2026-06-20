"""P0 hardening regressions (post-GA audit)."""
from __future__ import annotations



from aether.core.policy import normalize_file_roots
from aether.tools.registry import build_default_registry


def test_normalize_file_roots_expands_tilde(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/tester")
    roots = normalize_file_roots(["~"])
    assert roots == ["/Users/tester"]


def test_normalize_file_roots_narrows_users_default(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/tester")
    roots = normalize_file_roots(["/Users"])
    assert roots == ["/Users/tester"]


def test_delegation_tool_omitted_when_disabled(monkeypatch):
    from aether.core import config as config_mod

    base = config_mod.load_config(validate=False)
    patched = config_mod.Config(
        raw={**base.raw, "delegation": {"enabled": False}},
        anthropic_api_key=base.anthropic_api_key,
        openai_api_key=base.openai_api_key,
        api_keys=dict(base.api_keys),
    )
    monkeypatch.setattr(
        config_mod,
        "load_config",
        lambda *args, **kwargs: patched,
    )
    reg = build_default_registry()
    names = {s.name for s in reg.all_specs()}
    assert "delegate_to_coder" not in names


def test_ws_auth_ok_respects_token(monkeypatch):
    from sidecar.auth import ws_auth_ok

    monkeypatch.setenv("AETHER_SIDECAR_TOKEN", "secret")
    assert ws_auth_ok("Bearer secret") is True
    assert ws_auth_ok("Bearer wrong") is False
    assert ws_auth_ok(None) is False


def test_ws_auth_ok_open_when_no_token(monkeypatch):
    from sidecar.auth import ws_auth_ok

    monkeypatch.delenv("AETHER_SIDECAR_TOKEN", raising=False)
    assert ws_auth_ok(None) is True
