"""Approvals the user extended to the rest of one conversation.

Only for rule-of-two confirmations: the ones asked because the run read
untrusted content. Destructive actions, careful mode and the tools the
orchestrator never grants (see ``Agent._NEVER_GRANT``) always ask.

A grant covers either one website, for tools that only open a page there,
or one exact call (same tool, same arguments). Grants live in memory only:
a sidecar restart forgets them, and the user can revoke them at any time.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

# Tools that only load a page. A grant for them covers one host; tools that
# send data (fill a form, compose mail) never get more than the exact call.
HOST_SCOPED_TOOLS = frozenset({"browser_navigate", "browser_new_tab", "safari_open_url",
                               "open_url"})

GrantKey = tuple[str, str]


@dataclass(frozen=True)
class Grant:
    key: GrantKey
    label: str
    created: float = field(default_factory=time.time)


class SessionGrants:
    def __init__(self) -> None:
        self._grants: dict[GrantKey, Grant] = {}
        self._lock = threading.Lock()

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._grants

    def __len__(self) -> int:
        with self._lock:
            return len(self._grants)

    def add(self, key: GrantKey, label: str) -> None:
        with self._lock:
            self._grants.setdefault(key, Grant(key, label))

    def label(self, key: GrantKey) -> str:
        with self._lock:
            grant = self._grants.get(key)
            return grant.label if grant else ""

    def labels(self) -> list[str]:
        with self._lock:
            return [g.label for g in sorted(self._grants.values(), key=lambda g: g.created)]

    def clear(self) -> int:
        with self._lock:
            n = len(self._grants)
            self._grants.clear()
            return n


_sessions: dict[str, SessionGrants] = {}
_lock = threading.Lock()


def for_session(session_id: str) -> SessionGrants:
    with _lock:
        return _sessions.setdefault(session_id, SessionGrants())


def labels(session_id: str) -> list[str]:
    with _lock:
        grants = _sessions.get(session_id)
    return grants.labels() if grants else []


def revoke(session_id: str) -> int:
    with _lock:
        grants = _sessions.get(session_id)
    return grants.clear() if grants else 0


def reset() -> None:
    """Test hook."""
    with _lock:
        _sessions.clear()


def _host(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https"):
        return ""
    return (parts.hostname or "").lower()


def scope(name: str, args: dict, exact_key: GrantKey) -> tuple[GrantKey, str]:
    """(key, label) for a conversation-wide grant of this call."""
    if name in HOST_SCOPED_TOOLS:
        host = _host(str(args.get("url") or ""))
        if host:
            # One key for every page-opening tool: the grant is about the site.
            return ("open_page", "host:" + host), f"open pages on {host}"
    return exact_key, f"this exact {name} call"
