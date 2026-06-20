"""Optional sidecar API authentication (Phase 6).

Set ``AETHER_SIDECAR_TOKEN`` to require ``Authorization: Bearer <token>`` on
mutating endpoints and on ``GET /metrics`` / ``GET /dashboard`` when configured.
When unset, auth is disabled for local development.
"""
from __future__ import annotations

import os

from fastapi import HTTPException, Request


def sidecar_token() -> str | None:
    token = os.getenv("AETHER_SIDECAR_TOKEN", "").strip()
    return token or None


_LOCALHOST_ORIGINS = [
    "http://127.0.0.1",
    "http://localhost",
    "http://127.0.0.1:8765",
    "http://localhost:8765",
]


def cors_origins() -> list[str]:
    """CORS allowlist.

    When ``AETHER_SIDECAR_CORS_ORIGINS`` is unset, defaults to localhost-only
    (Phase 12). Set ``AETHER_SIDECAR_CORS_ORIGINS=*`` explicitly to allow all
    origins in trusted dev environments.
    """
    explicit = os.getenv("AETHER_SIDECAR_CORS_ORIGINS", "").strip()
    if explicit:
        if explicit == "*":
            return ["*"]
        return [o.strip() for o in explicit.split(",") if o.strip()]
    return list(_LOCALHOST_ORIGINS)


def bearer_matches(header_value: str, token: str) -> bool:
    return header_value == f"Bearer {token}"


def require_auth(request: Request) -> None:
    """Raise 401 when token is configured and request is not authorized."""
    token = sidecar_token()
    if not token:
        return
    auth = request.headers.get("Authorization", "")
    if bearer_matches(auth, token):
        return
    raise HTTPException(status_code=401, detail="Unauthorized: invalid or missing Bearer token")


def ws_auth_ok(authorization_header: str | None) -> bool:
    """Return True if WebSocket may proceed (no token configured or valid Bearer)."""
    token = sidecar_token()
    if not token:
        return True
    return bearer_matches(authorization_header or "", token)
