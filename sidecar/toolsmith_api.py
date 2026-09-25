"""The user's self-written tools: list, read the code, remove, roll back.

Creating a tool happens only inside an agent run (make_tool), where the user
approves it; these endpoints manage what is installed.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from aether.core.audit_log import AuditLog
from aether.core.config import load_config
from aether.tools.registry import DEFAULT_REGISTRY
from aether.tools import toolsmith_tools
from aether.toolsmith import executor, store
from aether.toolsmith.settings import Settings

from .auth import require_auth

router = APIRouter()


def _audit(event: str, name: str, summary: str) -> None:
    try:
        audit_cfg = load_config(validate=False).get("audit") or {}
        AuditLog.get(path=audit_cfg.get("path"), enabled=bool(audit_cfg.get("enabled", True))) \
            .record(event, tool=name, summary=summary[:200])
    except Exception:  # noqa: BLE001 — the audit must never break the API
        pass


def _installed(name: str) -> store.InstalledTool:
    tool = store.load(name)
    if tool is None:
        raise HTTPException(404, f"No self-written tool named {name!r}.")
    return tool


@router.get("/toolsmith/tools")
async def list_tools(_auth: None = Depends(require_auth)) -> dict[str, Any]:
    settings = Settings.load()
    tools = await asyncio.to_thread(store.list_tools)
    available, why = executor.can_run(settings)
    return {"enabled": settings.enabled, "available": available, "detail": why,
            "tools": [t.summary() for t in tools]}


@router.get("/toolsmith/tools/{name}")
async def get_tool(name: str, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    tool = _installed(name)
    return {**tool.summary(), "code": tool.code(), "how": tool.manifest.how}


@router.delete("/toolsmith/tools/{name}")
async def remove_tool(name: str, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    _installed(name)
    if not await asyncio.to_thread(store.remove, name):
        raise HTTPException(500, "Could not remove the tool.")
    DEFAULT_REGISTRY.unregister(name)
    _audit("tool_removed", name, "removed by the user")
    return {"removed": name}


@router.post("/toolsmith/tools/{name}/rollback")
async def rollback_tool(name: str, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    _installed(name)
    tool = await asyncio.to_thread(store.rollback, name)
    if tool is None:
        raise HTTPException(409, "There is no earlier version to go back to.")
    DEFAULT_REGISTRY.register(toolsmith_tools.spec_for(tool))
    _audit("tool_rolled_back", name, f"now v{tool.manifest.version}")
    return tool.summary()
