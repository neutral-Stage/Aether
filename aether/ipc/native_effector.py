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
        req = urllib.request.Request(url, method="GET", headers=_auth_headers())
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _auth_headers() -> dict[str, str]:
    token = os.getenv("AETHER_NATIVE_EFFECTOR_TOKEN", "").strip() or os.getenv(
        "AETHER_SIDECAR_TOKEN", ""
    ).strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def capture(*, display_id: int | None = None, max_edge: int = 0) -> dict[str, Any]:
    """ScreenCaptureKit screenshot of one display, with Aether's own windows hidden.

    Returns ``{path, width, height, display_id, scale, frame: [x, y, w, h]}``
    (frame in global top-left points). Raises when the app is not reachable.
    """
    query = []
    if display_id:
        query.append(f"display_id={int(display_id)}")
    if max_edge:
        query.append(f"max_edge={int(max_edge)}")
    url = f"{_base_url()}/capture" + (("?" + "&".join(query)) if query else "")
    req = urllib.request.Request(url, method="GET", headers=_auth_headers())
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "native capture failed"))
    return data


def pim(action: str, args: dict[str, Any] | None = None) -> Any:
    """Calendar/Reminders/Contacts via the Swift app's EventKit/Contacts access
    (``POST /pim``). Raises ``RuntimeError`` when the app refuses or can't be
    reached; callers should fold that into "the Aether app must be running".
    """
    url = f"{_base_url()}/pim"
    body = json.dumps({"action": action, "args": args or {}}).encode("utf-8")
    headers = {"Content-Type": "application/json", **_auth_headers()}
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode("utf-8"))
        except (ValueError, OSError):
            raise RuntimeError(f"native effector /pim failed (HTTP {e.code})") from e
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "native /pim failed"))
    return data.get("result")


def invoke_native(tool: str, args: dict[str, Any]) -> str:
    """Invoke click/type on Swift effector server."""
    url = f"{_base_url()}/invoke"
    body = json.dumps({"tool": tool, "args": args}).encode("utf-8")
    headers = {"Content-Type": "application/json", **_auth_headers()}
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
