"""Meeting notes (see docs/MEETINGS.md).

The app asks for consent, then streams ~30 s WAV chunks of the meeting app's
audio ("them") and the microphone ("me"). Each chunk is transcribed and
deleted; only the text is kept. Stopping writes a summary, decisions and action
items.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from aether import meetings
from aether.core.audit_log import AuditLog
from aether.core.config import load_config
from aether.core.policy import Policy, PolicyConfig
from aether.core.security import redact_tokens, wrap_untrusted
from aether.meetings.store import CHANNELS

from .auth import require_auth

router = APIRouter()
MAX_CHUNK_BYTES = 25 * 1024 * 1024


class StartMeeting(BaseModel):
    title: str = ""
    app: str = ""


def _settings(cfg: Any) -> dict[str, Any]:
    m = cfg.get("meetings") or {}
    return {"transcription": str(m.get("transcription", "local")),
            "summarize": bool(m.get("summarize", True))}


def transcription(cfg: Any) -> tuple[str, str]:
    """(engine, problem): the engine meeting audio goes to, and why it can't be used ("" if it can)."""
    from aether.voice.stt_local import LocalSTT

    mode = _settings(cfg)["transcription"]
    engine = "local" if mode == "local" else str(cfg.stt)
    if engine == "local":
        if LocalSTT().available():
            return "local", ""
        return "local", ("Local transcription needs mlx-whisper or pywhispercpp "
                         "(pip install mlx-whisper), or set meetings.transcription: cloud.")
    if engine == "groq" and cfg.groq_api_key:
        return "groq", ""
    if engine == "openai" and cfg.openai_api_key:
        return "openai", ""
    return engine, "Cloud transcription needs voice.stt set to groq or openai, with its key."


def _transcribe(cfg: Any, engine: str, wav: bytes) -> str:
    from aether.voice.stt import STT

    fd, name = tempfile.mkstemp(prefix="aether-meeting-", suffix=".wav")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(wav)
        stt = STT(engine=engine, model=cfg.stt_model, openai_api_key=cfg.openai_api_key,
                  groq_api_key=cfg.groq_api_key)
        return stt.transcribe_file(name)
    finally:
        Path(name).unlink(missing_ok=True)   # audio is never kept


def _summarize(cfg: Any, transcript: str) -> dict[str, Any] | None:
    from . import talk_api

    policy = Policy(PolicyConfig(redact_secrets=True))
    clean, _ = redact_tokens(policy.redact_text(transcript))
    client = talk_api._client(cfg)  # noqa: SLF001
    resp = client.step(meetings.SUMMARY_SYSTEM,
                       [{"role": "user", "content": wrap_untrusted(clean, "transcript")}], [])
    return meetings.parse_notes(str(getattr(resp, "text", "") or ""))


def _meeting_or_404(mid: str) -> dict[str, Any]:
    m = meetings.get_store().meeting(mid)
    if m is None:
        raise HTTPException(404, "Unknown meeting")
    return m


@router.get("/meetings/transcription")
async def transcription_status(_auth: None = Depends(require_auth)) -> dict[str, Any]:
    """What the consent prompt tells the user: where audio goes, whether notes are written."""
    cfg = load_config(validate=False)
    engine, problem = transcription(cfg)
    s = _settings(cfg)
    return {"engine": engine, "ready": not problem, "message": problem,
            "summarize": s["summarize"] and cfg.has_cloud_llm()}


@router.post("/meetings")
async def start(body: StartMeeting, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    cfg = load_config(validate=False)
    engine, problem = transcription(cfg)
    if problem:
        raise HTTPException(501, problem)
    title = body.title.strip() or (f"{body.app} meeting" if body.app else "Meeting")
    mid = await asyncio.to_thread(meetings.get_store().create, title, body.app)
    AuditLog.get().record("meeting_started", summary=title,
                          extra={"meeting_id": mid, "app": body.app, "transcription": engine})
    return {"id": mid, "engine": engine, "title": title}


@router.post("/meetings/{mid}/audio")
async def audio(mid: str, request: Request, channel: str, offset_s: float = 0.0,
                _auth: None = Depends(require_auth)) -> dict[str, Any]:
    if channel not in CHANNELS:
        raise HTTPException(400, "channel must be 'them' or 'me'")
    m = _meeting_or_404(mid)
    if m["ended"] is not None:
        raise HTTPException(409, "This meeting has ended")
    wav = await request.body()
    if len(wav) > MAX_CHUNK_BYTES:
        raise HTTPException(413, "Audio chunk too large")
    if not wav.startswith(b"RIFF") or wav[8:12] != b"WAVE":
        raise HTTPException(400, "Expected a WAV file")
    cfg = load_config(validate=False)
    engine, problem = transcription(cfg)
    if problem:
        raise HTTPException(501, problem)
    try:
        text = await asyncio.to_thread(_transcribe, cfg, engine, wav)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Transcription failed: {str(e)[:200]}") from e
    ts = float(m["started"]) + max(0.0, offset_s)
    await asyncio.to_thread(meetings.get_store().add_segment, mid, channel, text, ts)
    return {"text": text}


@router.post("/meetings/{mid}/stop")
async def stop(mid: str, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    m = _meeting_or_404(mid)
    store = meetings.get_store()
    await asyncio.to_thread(store.end, mid)
    segments = await asyncio.to_thread(store.segments, mid)
    AuditLog.get().record("meeting_stopped", summary=m["title"],
                          extra={"meeting_id": mid, "segments": len(segments)})
    if not segments:
        return {"id": mid, "text": "No speech was transcribed.", "notes": None}
    cfg = load_config(validate=False)
    transcript = meetings.transcript_for_model(segments, float(m["started"]))
    notes = None
    message = ""
    if _settings(cfg)["summarize"] and cfg.has_cloud_llm():
        try:
            notes = await asyncio.to_thread(_summarize, cfg, transcript)
        except Exception as e:  # noqa: BLE001
            message = f"The transcript is saved, but the notes failed: {str(e)[:160]}"
        if notes is None and not message:
            message = "The transcript is saved, but the model's notes were not usable."
    if notes:
        await asyncio.to_thread(store.set_notes, mid, notes["summary"], notes)
        text = meetings.render_notes(m["title"], float(m["started"]), notes)
    else:
        text = message or "The transcript is saved (notes are off: meetings.summarize)."
    return {"id": mid, "text": text, "notes": notes}


@router.get("/meetings")
async def list_meetings(limit: int = 20, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    rows = await asyncio.to_thread(meetings.get_store().list, max(1, min(limit, 100)))
    return {"meetings": rows}


@router.get("/meetings/{mid}")
async def get_meeting(mid: str, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    m = _meeting_or_404(mid)
    segments = await asyncio.to_thread(meetings.get_store().segments, mid)
    return {**m, "segments": segments,
            "transcript": meetings.transcript_for_model(segments, float(m["started"]))}


@router.delete("/meetings/{mid}")
async def delete_meeting(mid: str, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    if not await asyncio.to_thread(meetings.get_store().delete, mid):
        raise HTTPException(404, "Unknown meeting")
    AuditLog.get().record("meeting_deleted", extra={"meeting_id": mid})
    return {"deleted": True}
