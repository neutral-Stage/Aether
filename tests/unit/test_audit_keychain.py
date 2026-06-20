"""Audit HMAC key resolution (Keychain bridge, Phase 12+)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from aether.core.audit_log import AuditLog, resolve_audit_hmac_key


class TestAuditKeyResolution:
    def test_env_over_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        key_file = tmp_path / ".audit_hmac_key"
        key_file.write_bytes(b"file-key")
        monkeypatch.setenv("AETHER_AUDIT_KEY", "env-secret-key")
        with patch("aether.core.audit_log._load_from_keychain", return_value=None):
            key, source = resolve_audit_hmac_key()
        assert source == "env"
        assert key == b"env-secret-key"

    def test_keychain_preferred(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AETHER_AUDIT_KEY", "env-secret-key")
        with patch(
            "aether.core.audit_log._load_from_keychain",
            return_value=b"keychain-bytes",
        ):
            key, source = resolve_audit_hmac_key()
        assert source == "keychain"
        assert key == b"keychain-bytes"

    def test_audit_log_exposes_key_source(self, tmp_path: Path) -> None:
        log = AuditLog(
            path=tmp_path / "audit.jsonl",
            enabled=True,
            hmac_key=b"test-key-32-bytes-long!!!!!!!!!",
        )
        assert log.key_source == "provided"
