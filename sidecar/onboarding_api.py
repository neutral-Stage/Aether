"""Onboarding interview and tour.

Four short questions about the owner's work, stored as profile memories that
every agent run sees ("About the user, in their own words"), and a "what can
I say" tour built from the knowledge packs of apps that are installed.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aether.core.config import load_config

from .auth import require_auth

router = APIRouter()

QUESTIONS: list[dict[str, str]] = [
    {"id": "work", "question": "What do you do for work?",
     "placeholder": "e.g. I edit videos for a small agency", "prefix": "Work: "},
    {"id": "apps", "question": "Which apps do you use most?",
     "placeholder": "e.g. Final Cut, Mail, Notion, Slack", "prefix": "Apps they use most: "},
    {"id": "help", "question": "What would you most like help with?",
     "placeholder": "e.g. filing invoices, renaming exports, inbox triage",
     "prefix": "Wants help with: "},
    {"id": "style", "question": "How should Aether talk to you?",
     "placeholder": "e.g. short and casual, or detailed", "prefix": "How they like answers: "},
]

# Always in the tour: the ways to reach Aether, whatever is installed.
BASICS: list[dict[str, str]] = [
    {"say": "Hold ⌃⌥, point at something, and ask \"what is this?\"", "kind": "talk",
     "app": ""},
    {"say": "Show me how to add a printer", "kind": "guide", "app": "System Settings"},
    {"say": "Open my Downloads folder", "kind": "do", "app": "Finder"},
]


class ProfileRequest(BaseModel):
    answers: dict[str, str]


def _memory() -> Any:
    cfg = load_config(validate=False)
    if not cfg.memory_enabled:
        return None
    from aether.memory.store import MemoryStore

    mem_cfg = cfg.get("memory") or {}
    return MemoryStore(cfg.memory_db_path,
                       embedding_provider=str(mem_cfg.get("embedding_provider", "hash")),
                       openai_api_key=cfg.openai_api_key,
                       openai_model=str(mem_cfg.get("openai_model", "text-embedding-3-small")),
                       local_model=str(mem_cfg.get("local_model", "all-MiniLM-L6-v2")))


@router.get("/onboarding/questions")
async def questions(_auth: None = Depends(require_auth)) -> dict[str, Any]:
    answers: dict[str, str] = {}
    store = await asyncio.to_thread(_memory)
    if store is not None:
        prefixes = {q["id"]: q["prefix"] for q in QUESTIONS}
        for e in await asyncio.to_thread(store.profile):
            key = str(e.metadata.get("question", ""))
            if key in prefixes and key not in answers:
                answers[key] = e.text.removeprefix(prefixes[key])
        store.close()
    return {"questions": [{k: q[k] for k in ("id", "question", "placeholder")}
                          for q in QUESTIONS], "answers": answers,
            "memory_enabled": store is not None}


@router.post("/onboarding/profile")
async def save_profile(body: ProfileRequest, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    known = {q["id"]: q for q in QUESTIONS}
    unknown = sorted(set(body.answers) - set(known))
    if unknown:
        raise HTTPException(400, f"Unknown question(s): {', '.join(unknown)}")
    store = await asyncio.to_thread(_memory)
    if store is None:
        raise HTTPException(409, "Memory is off (memory.enabled in config.yaml).")
    saved, refused, cleared = [], [], []
    try:
        for key, text in body.answers.items():
            answer = " ".join((text or "").split())
            if not answer:
                await asyncio.to_thread(store.set_profile, key, "")
                cleared.append(key)
                continue
            row = await asyncio.to_thread(store.set_profile, key, known[key]["prefix"] + answer)
            (saved if row else refused).append(key)
    finally:
        store.close()
    return {"saved": saved, "refused": refused, "cleared": cleared}


def tour_examples(installed: dict[str, str] | None = None, limit: int = 8) -> list[dict[str, str]]:
    """Things to try, from the packs of installed apps (one per recipe, one per app first)."""
    from aether.core import fast_router
    from aether.knowledge import loader

    installed = fast_router.installed_apps() if installed is None else installed
    out = list(BASICS)
    seen_apps = {e["app"] for e in out}
    per_app: list[dict[str, str]] = []
    for key in loader.list_packs():
        pack = loader._load_pack_file(key) or {}  # noqa: SLF001
        app = str(pack.get("app") or "")
        if not app or app.lower() not in installed:
            continue
        for name, recipe in (pack.get("verified_recipes") or {}).items():
            phrases = recipe.get("match") or []
            if not phrases:
                continue
            example = str(recipe.get("example") or phrases[0])
            per_app.append({"say": example[:1].upper() + example[1:], "kind": "do", "app": app,
                            "recipe": f"{key}.{name}"})
    first: list[dict[str, str]] = []     # one example per app before any repeats
    rest: list[dict[str, str]] = []
    for e in per_app:
        (rest if e["app"] in seen_apps else first).append(e)
        seen_apps.add(e["app"])
    return (out + first + rest)[:limit]


@router.get("/onboarding/tour")
async def tour(_auth: None = Depends(require_auth)) -> dict[str, Any]:
    return {"examples": await asyncio.to_thread(tour_examples)}
