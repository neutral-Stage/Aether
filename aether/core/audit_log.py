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

from .paths import ROOT, data_dir, resolve_data_path  # noqa: F401

DEFAULT_PATH = data_dir() / "audit.jsonl"  # informational; resolved per instance


def _key_path() -> Path:
    return data_dir() / ".audit_hmac_key"
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
    key_path = _key_path()
    if key_path.exists():
        return key_path.read_bytes(), "file"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    key_path.write_bytes(key)
    try:
        key_path.chmod(0o600)
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
        self.path = resolve_data_path(path, "audit.jsonl")
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

        # Link, sign and write under one lock: two threads that read the same
        # previous hash would fork the chain, and verification would then fail.
        with self._lock:
            entry["prev_hash"] = self._prev_hash
            canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"))
            entry["hmac"] = self._sign(canonical)
            entry["record_hash"] = hashlib.sha256(
                (self._prev_hash + canonical + entry["hmac"]).encode("utf-8")
            ).hexdigest()
            try:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                return None
            self._prev_hash = entry["record_hash"]
        return entry["record_hash"]

    def _lines(self) -> list[str]:
        return [ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def verify_chain(self, max_records: int | None = None) -> tuple[bool, str]:
        """Verify the HMAC and hash chain of the whole log, or of its last
        `max_records` (the first of those is taken to link to what came before)."""
        if not self.path.exists():
            return True, "empty"
        try:
            lines = self._lines()
        except OSError as exc:
            return False, str(exc)
        total = len(lines)
        window = lines[-max_records:] if max_records else lines
        start = total - len(window)
        prev: str | None = "" if start == 0 else None
        count = 0
        for n, line in enumerate(window, start=start + 1):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                return False, f"record {n} is not readable"
            if not isinstance(rec, dict) or not isinstance(rec.get("hmac"), str):
                return False, f"record {n} is not an audit record"
            if prev is None:
                prev = str(rec.get("prev_hash", ""))
            if rec.get("prev_hash") != prev:
                return False, f"chain break at record {n} (ts={rec.get('ts')})"
            body = {k: v for k, v in rec.items() if k not in ("hmac", "record_hash")}
            canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
            expected = self._sign(canonical)
            if not hmac.compare_digest(expected, rec["hmac"]):
                return False, f"signature mismatch at record {n} (ts={rec.get('ts')})"
            rh = hashlib.sha256(
                (prev + canonical + rec["hmac"]).encode("utf-8")
            ).hexdigest()
            if rh != rec.get("record_hash"):
                return False, f"record_hash mismatch at record {n} (ts={rec.get('ts')})"
            prev = rec["record_hash"]
            count += 1
        scope = f"{count} records" if start == 0 else f"last {count} of {total} records"
        return True, f"ok ({scope})"

    def recent(self, limit: int = 200, *, query: str = "", event: str = "",
               run_id: str = "") -> list[dict[str, Any]]:
        """Newest first, filtered; signature fields are left out, `id` is a short hash."""
        if not self.path.exists():
            return []
        try:
            lines = self._lines()
        except OSError:
            return []
        q = query.strip().lower()
        out: list[dict[str, Any]] = []
        for line in reversed(lines):
            if q and q not in line.lower():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            if event and rec.get("event") != event:
                continue
            if run_id and rec.get("run_id") != run_id:
                continue
            item = {k: v for k, v in rec.items() if k not in ("hmac", "record_hash", "prev_hash")}
            item["id"] = str(rec.get("record_hash", ""))[:12]
            out.append(item)
            if len(out) >= limit:
                break
        return out


def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
    """Truncate large argument values for audit storage."""
    out: dict[str, Any] = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 500:
            out[k] = v[:500] + "…"
        else:
            out[k] = v
    return out
