"""HTTP client for Swift native effectors (Phase 8).

When ``beta.native_effectors`` is enabled, reflexive click/type can route to the
macOS app HTTP server (default ``127.0.0.1:8766``) for lower latency.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8766
_TIMEOUT_SEC = 5.0


def _base_url() -> str:
    host = os.getenv("AETHER_NATIVE_EFFECTOR_HOST", _DEFAULT_HOST)
    port = os.getenv("AETHER_NATIVE_EFFECTOR_PORT", str(_DEFAULT_PORT))
    return f"http://{host}:{port}"


def available() -> bool:
    """Best-effort health check — True when Swift effector server responds."""
    url = f"{_base_url()}/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def invoke_native(tool: str, args: dict[str, Any]) -> str:
    """Invoke click/type on Swift effector server."""
    url = f"{_base_url()}/invoke"
    body = json.dumps({"tool": tool, "args": args}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = os.getenv("AETHER_NATIVE_EFFECTOR_TOKEN", "").strip() or os.getenv(
        "AETHER_SIDECAR_TOKEN", ""
    ).strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "native effector failed"))
    return str(data.get("result", "OK"))
