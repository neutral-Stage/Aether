"""Conversation endpoints: list, read, start and delete sessions.

POST /run takes a session_id to continue a conversation; without one it
starts a new session and reports its id in the run_start and done events.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import session_store
from .auth import require_auth

router = APIRouter()


class NewSession(BaseModel):
    title: str = ""


@router.get("/sessions")
async def list_sessions(limit: int = 50, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    rows = await asyncio.to_thread(session_store.list_sessions, max(1, min(limit, 200)))
    return {"sessions": rows}


@router.post("/sessions")
async def new_session(body: NewSession, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    sid = await asyncio.to_thread(session_store.create_session, body.title)
    return {"session_id": sid}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    data = await asyncio.to_thread(session_store.get_session, session_id)
    if data is None:
        raise HTTPException(404, "Unknown session")
    return data


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    if not await asyncio.to_thread(session_store.delete_session, session_id):
        raise HTTPException(404, "Unknown session")
    return {"status": "deleted"}
