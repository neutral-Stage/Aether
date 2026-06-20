"""Safe error responses for the sidecar (Phase 12 — no secret leakage)."""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*\S+"),
    re.compile(r"Bearer\s+\S+"),
    re.compile(r"eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+"),
]


def redact_error_message(message: str) -> str:
    out = message
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    if len(out) > 500:
        out = out[:500] + "…"
    return out


def structured_error(
    *,
    code: str,
    message: str,
    status_code: int = 500,
    request_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ok": False,
        "error": {
            "code": code,
            "message": redact_error_message(message),
        },
    }
    if request_id:
        body["request_id"] = request_id
    body["status_code"] = status_code
    return body
