"""Append-only signed audit log (§6.7 FR-29, Phase 5).

Records percept summaries, decisions, actions, and confirmations to
`data/audit.jsonl` with an HMAC hash chain for tamper detection.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "data" / "audit.jsonl"
_KEY_PATH = ROOT / "data" / ".audit_hmac_key"
_KEYCHAIN_SERVICE = "com.aether.audit"
_KEYCHAIN_ACCOUNT = "hmac-key"


def _load_from_keychain() -> bytes | None:
    """Read audit HMAC key from macOS Keychain (Swift ``AuditKeychain`` bridge)."""
    if sys.platform != "darwin":
        return None
    try:
        proc = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                _KEYCHAIN_SERVICE,
                "-a",
                _KEYCHAIN_ACCOUNT,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            value = proc.stdout.strip()
            if value:
                return value.encode("utf-8")
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def resolve_audit_hmac_key() -> tuple[bytes, str]:
    """Return ``(key, source)`` — priority: keychain → env → file → generated."""
    keychain = _load_from_keychain()
    if keychain:
        return keychain, "keychain"
    env = os.getenv("AETHER_AUDIT_KEY")
    if env:
        return env.encode("utf-8"), "env"
    if _KEY_PATH.exists():
        return _KEY_PATH.read_bytes(), "file"
    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    _KEY_PATH.write_bytes(key)
    try:
        _KEY_PATH.chmod(0o600)
    except OSError:
        pass
    return key, "generated"


class AuditLog:
    """Thread-safe append-only JSONL audit log with HMAC chain."""

    _instance: AuditLog | None = None
    _class_lock = threading.Lock()

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        enabled: bool = True,
        hmac_key: bytes | None = None,
    ) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self.enabled = enabled
        self._lock = threading.Lock()
        self._prev_hash = ""
        if hmac_key is not None:
            self._key = hmac_key
            self._key_source = "provided"
        else:
            self._key, self._key_source = resolve_audit_hmac_key()
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._prev_hash = self._read_last_hash()

    @classmethod
    def get(cls, **kwargs: Any) -> AuditLog:
        with cls._class_lock:
            if cls._instance is None:
                cls._instance = AuditLog(**kwargs)
            return cls._instance

    @classmethod
    def configure(cls, **kwargs: Any) -> AuditLog:
        with cls._class_lock:
            cls._instance = AuditLog(**kwargs)
            return cls._instance

    @property
    def key_source(self) -> str:
        """Where the HMAC key was loaded from (keychain, env, file, generated, provided)."""
        return self._key_source

    def _read_last_hash(self) -> str:
        if not self.path.exists():
            return ""
        last_line = ""
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        last_line = line
        except OSError:
            return ""
        if not last_line:
            return ""
        try:
            rec = json.loads(last_line)
            return str(rec.get("record_hash", ""))
        except json.JSONDecodeError:
            return ""

    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def record(
        self,
        event_type: str,
        *,
        run_id: str | None = None,
        summary: str | None = None,
        tool: str | None = None,
        tool_args: dict[str, Any] | None = None,
        route_tier: str | None = None,
        confirmed: bool | None = None,
        injection_severity: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str | None:
        if not self.enabled:
            return None
        entry: dict[str, Any] = {
            "ts": time.time(),
            "event": event_type,
            "prev_hash": self._prev_hash,
        }
        if run_id:
            entry["run_id"] = run_id
        if summary:
            entry["summary"] = summary[:2000]
        if tool:
            entry["tool"] = tool
        if tool_args is not None:
            entry["tool_args"] = _safe_args(tool_args)
        if route_tier:
            entry["route_tier"] = route_tier
        if confirmed is not None:
            entry["confirmed"] = confirmed
        if injection_severity:
            entry["injection_severity"] = injection_severity
        if extra:
            entry["extra"] = extra

        canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        entry["hmac"] = self._sign(canonical)
        entry["record_hash"] = hashlib.sha256(
            (self._prev_hash + canonical + entry["hmac"]).encode("utf-8")
        ).hexdigest()

        with self._lock:
            self._prev_hash = entry["record_hash"]
            try:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                return None
        return entry["record_hash"]

    def verify_chain(self, max_records: int = 500) -> tuple[bool, str]:
        """Verify HMAC + hash chain for recent records."""
        if not self.path.exists():
            return True, "empty"
        prev = ""
        count = 0
        try:
            lines = self.path.read_text(encoding="utf-8").strip().splitlines()
        except OSError as exc:
            return False, str(exc)
        for line in lines[-max_records:]:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("prev_hash") != prev:
                return False, f"chain break at ts={rec.get('ts')}"
            body = {k: v for k, v in rec.items() if k not in ("hmac", "record_hash")}
            canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
            expected = self._sign(canonical)
            if not hmac.compare_digest(expected, rec.get("hmac", "")):
                return False, f"hmac mismatch at ts={rec.get('ts')}"
            rh = hashlib.sha256(
                (prev + canonical + rec["hmac"]).encode("utf-8")
            ).hexdigest()
            if rh != rec.get("record_hash"):
                return False, f"record_hash mismatch at ts={rec.get('ts')}"
            prev = rec["record_hash"]
            count += 1
        return True, f"ok ({count} records)"


def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
    """Truncate large argument values for audit storage."""
    out: dict[str, Any] = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 500:
            out[k] = v[:500] + "…"
        else:
            out[k] = v
    return out
