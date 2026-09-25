"""Quick skills: list, save, delete, run (see aether/quick_skills.py).

The app captures the selection, clipboard or spoken words and delivers the
result (paste, clipboard, speak, show); the sidecar captures screen text and
screenshots, calls the model, and appends to files.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aether import quick_skills as qs
from aether.core.config import load_config
from aether.core.policy import Policy, PolicyConfig, normalize_file_roots

from .auth import require_auth

router = APIRouter()


class SkillBody(BaseModel):
    id: str | None = None
    name: str
    prompt: str
    capture: str = "selection"
    destination: str = "show"
    hotkey: str = ""
    file_path: str = ""


class RunBody(BaseModel):
    selection: str = ""
    clipboard: str = ""
    spoken: str = ""


def _policy() -> Policy:
    raw = load_config(validate=False).raw
    roots = normalize_file_roots((raw.get("policy") or {}).get("approved_file_roots"))
    return Policy(PolicyConfig(redact_secrets=True, approved_file_roots=roots))


def _screen_text() -> str:
    from aether.perception import accessibility as ax

    data = ax.screen_context(max_elements=120, capture_handles=False)
    return f"App: {data.get('frontmost_app', '')}\n{data.get('rendered', '')}"


@router.get("/quick-skills")
async def list_skills(_auth: None = Depends(require_auth)) -> dict[str, Any]:
    skills = await asyncio.to_thread(qs.load)
    return {"skills": [s.__dict__ for s in skills], "captures": list(qs.CAPTURES),
            "destinations": list(qs.DESTINATIONS), "hotkeys": list(qs.HOTKEYS)}


@router.post("/quick-skills")
async def save_skill(body: SkillBody, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    data = body.model_dump()
    if not data.get("id"):
        data.pop("id")
    skill = qs.QuickSkill.from_dict(data)
    if skill.destination == "file" and skill.file_path \
            and not _policy().allows_file_path(skill.file_path):
        raise HTTPException(400, "That file is outside the approved folders.")
    try:
        saved = await asyncio.to_thread(qs.upsert, skill)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return saved.__dict__


@router.delete("/quick-skills/{skill_id}")
async def delete_skill(skill_id: str, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    if not await asyncio.to_thread(qs.delete, skill_id):
        raise HTTPException(404, "No such skill.")
    return {"deleted": skill_id}


@router.post("/quick-skills/{skill_id}/run")
async def run_skill(skill_id: str, body: RunBody,
                    _auth: None = Depends(require_auth)) -> dict[str, Any]:
    skill = await asyncio.to_thread(qs.get, skill_id)
    if skill is None:
        raise HTTPException(404, "No such skill.")
    cfg = load_config()
    if not cfg.has_cloud_llm():
        raise HTTPException(400, "Quick skills need a cloud model key.")
    policy = _policy()
    image = None
    material = {"selection": body.selection, "clipboard": body.clipboard,
                "spoken": body.spoken}.get(skill.capture, "")
    if skill.capture == "screen_text":
        material = await asyncio.to_thread(_screen_text)
    elif skill.capture == "screenshot":
        from aether.perception import screen

        image = await asyncio.to_thread(screen.try_capture_to_file)
        if not image:
            raise HTTPException(409, "Couldn't capture the screen (Screen Recording allowed?).")
    if skill.capture not in ("none", "screenshot") and not material.strip():
        raise HTTPException(400, {"selection": "Select some text first.",
                                  "clipboard": "The clipboard is empty.",
                                  "spoken": "Nothing was said."}.get(skill.capture,
                                                                    "Nothing to work on."))
    from . import talk_api

    client = talk_api._client(cfg)  # noqa: SLF001
    try:
        text = await asyncio.to_thread(qs.run, skill, client,
                                       material=policy.redact_text(material), image_path=image)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"The skill failed: {str(e)[:200]}") from e
    if not text:
        raise HTTPException(502, "The model returned nothing.")
    out: dict[str, Any] = {"text": text, "destination": skill.destination, "name": skill.name}
    if skill.destination == "file":
        if not policy.allows_file_path(skill.file_path):
            raise HTTPException(400, "That file is outside the approved folders.")
        out["file"] = await asyncio.to_thread(qs.append_to_file, skill, text)
    return out
