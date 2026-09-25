"""Polite proactive hints (off by default; see docs/HINTS.md).

The app calls POST /hints/check while the user is idle. The sidecar reads the
front window's text itself, through the same privacy rules as screen memory,
and asks the model only when the throttle allows it. A hint is shown only if it
matches the schema, is confident enough, and its category is not cooling down
or muted.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aether.core.audit_log import AuditLog
from aether.core.config import load_config
from aether.core.paths import data_dir
from aether.core.policy import Policy, PolicyConfig, looks_like_command_line
from aether.core.security import (InjectionSeverity, redact_tokens, scan_injection,
                                  wrap_untrusted)
from aether.hints import CATEGORIES, HINT_SYSTEM, Hint, HintSettings, HintThrottle, parse_hint

from .auth import require_auth

router = APIRouter()
_throttle: HintThrottle | None = None
_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)|\b[a-z0-9-]+\.(?:com|net|org|io|app|xyz)\b")
MIN_TEXT = 40


class CheckRequest(BaseModel):
    idle_s: float = 0.0
    typing: bool = False


class MuteRequest(BaseModel):
    category: str
    muted: bool = True


def _muted_path():  # noqa: ANN202
    return data_dir() / "hints.json"


def _load_muted() -> set[str]:
    try:
        data = json.loads(_muted_path().read_text(encoding="utf-8"))
        return {c for c in data.get("muted", []) if c in CATEGORIES}
    except (OSError, ValueError, AttributeError):
        return set()


def _save_muted(muted: set[str]) -> None:
    path = _muted_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"muted": sorted(muted)}), encoding="utf-8")


def throttle(raw: dict | None = None) -> HintThrottle:
    """The process's throttle, with settings re-read so config edits apply at once."""
    global _throttle
    settings = HintSettings.from_raw(raw if raw is not None else load_config(validate=False).raw)
    if _throttle is None:
        _throttle = HintThrottle(settings, muted=_load_muted())
    else:
        _throttle.settings = settings
    return _throttle


def reset() -> None:
    """Test hook."""
    global _throttle
    _throttle = None


def read_front(raw: dict) -> tuple[Any, str]:
    """(window state, its text) when the privacy rules allow reading it, else (None, why)."""
    from aether.screen_memory.privacy import decide
    from aether.screen_memory.recorder import RecorderSettings, probe_front, read_window_text

    state = probe_front()
    privacy = RecorderSettings.from_raw(raw).privacy
    privacy.paused = False          # pausing screen memory doesn't turn hints off
    decision = decide(state, privacy)
    if not decision.allowed:
        return None, decision.reason
    text, _source = read_window_text(state, ocr_fallback=False)
    return state, text


def safe_to_show(hint: Hint) -> bool:
    """The hint came from a model that read untrusted screen text: no links, no
    commands to run, nothing that reads like instructions to an assistant."""
    both = f"{hint.hint} {hint.reason}"
    if _URL_RE.search(both) or looks_like_command_line(hint.hint):
        return False
    return scan_injection(both).severity not in (InjectionSeverity.HIGH, InjectionSeverity.MEDIUM)


def _client(cfg: Any) -> Any:
    from . import talk_api

    return talk_api._client(cfg)  # noqa: SLF001


@router.get("/hints/status")
async def status(_auth: None = Depends(require_auth)) -> dict[str, Any]:
    th = throttle()
    return {"enabled": th.settings.enabled, "muted": sorted(th.muted),
            "shown_today": th.shown_today(), "daily_cap": th.settings.daily_cap,
            "min_confidence": th.settings.min_confidence}


@router.post("/hints/mute")
async def mute(body: MuteRequest, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    if body.category not in CATEGORIES:
        raise HTTPException(400, f"category must be one of {', '.join(CATEGORIES)}")
    th = throttle()
    if body.muted:
        th.muted.add(body.category)
    else:
        th.muted.discard(body.category)
    await asyncio.to_thread(_save_muted, set(th.muted))
    return {"muted": sorted(th.muted)}


@router.post("/hints/check")
async def check(body: CheckRequest, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    cfg = load_config(validate=False)
    th = throttle(cfg.raw)
    if not th.settings.enabled:
        return {"hint": None, "reason": "off"}
    if body.typing or body.idle_s < th.settings.idle_s:
        # Cheap refusals first, before reading the screen.
        return {"hint": None, "reason": "typing" if body.typing else "not idle"}
    if not cfg.has_cloud_llm():
        return {"hint": None, "reason": "no model key"}
    state, text = await asyncio.to_thread(read_front, cfg.raw)
    if state is None:
        return {"hint": None, "reason": text}
    policy = Policy(PolicyConfig(redact_secrets=True))
    title = policy.redact_text(str(state.window_title or ""))
    text, _hidden = redact_tokens(policy.redact_text(text), entropy=True)
    if len(text.strip()) < MIN_TEXT:
        return {"hint": None, "reason": "nothing to read"}
    fingerprint = hashlib.sha1(f"{state.bundle_id}|{title}|{text}".encode()).hexdigest()
    ok, reason = th.should_query(idle_s=body.idle_s, typing=body.typing, fingerprint=fingerprint)
    if not ok:
        return {"hint": None, "reason": reason}
    th.mark_queried(fingerprint)
    content = (f"App: {state.app}\nWindow: {title}\n"
               + wrap_untrusted(text[:4000], "screen_text"))
    try:
        resp = await asyncio.to_thread(_client(cfg).step, HINT_SYSTEM,
                                       [{"role": "user", "content": content}], [])
    except Exception:  # noqa: BLE001 — a hint is never worth an error
        return {"hint": None, "reason": "model unavailable"}
    hint = parse_hint(str(getattr(resp, "text", "") or ""))
    if hint is None:
        return {"hint": None, "reason": "no hint"}
    if not safe_to_show(hint):
        return {"hint": None, "reason": "unsafe hint dropped"}
    shown, reason = th.accept(hint)
    if not shown:
        return {"hint": None, "reason": reason}
    AuditLog.get().record("hint", summary=hint.hint,
                          extra={"category": hint.category, "confidence": hint.confidence,
                                 "app": state.app})
    return {"hint": hint.as_dict(), "reason": "shown"}
