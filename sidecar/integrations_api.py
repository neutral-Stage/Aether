"""Integrations catalog: status and on/off, for the Aether window's
Integrations panel. No accounts or tokens — turning one on just lets the
matching tools run; see ``aether/integrations`` and ``docs/INTEGRATIONS.md``.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aether import integrations
from aether.core.audit_log import AuditLog

from .auth import require_auth

router = APIRouter()


class SetIntegrationRequest(BaseModel):
    enabled: bool


@router.get("/integrations")
async def list_integrations(_auth: None = Depends(require_auth)) -> dict[str, Any]:
    """The catalog with each entry's on/off state, plus what the Swift app's
    EventKit/Contacts access currently reports (``null`` when it can't be
    reached — the app isn't running, or has no bearer token configured)."""
    catalog = await asyncio.to_thread(integrations.catalog_status)
    os_status: dict[str, Any] | None
    try:
        from aether.ipc import native_effector

        os_status = await asyncio.to_thread(native_effector.pim, "status")
    except Exception:  # noqa: BLE001 — app not running/reachable
        os_status = None
    return {"integrations": catalog, "os_status": os_status}


@router.post("/integrations/{integration_id}")
async def set_integration(integration_id: str, body: SetIntegrationRequest,
                          _auth: None = Depends(require_auth)) -> dict[str, Any]:
    entry = await asyncio.to_thread(integrations.set_enabled, integration_id, body.enabled)
    if not entry:
        raise HTTPException(404, f"no such integration: {integration_id}")
    AuditLog.get().record(
        "integration",
        summary=f"{entry['name']} {'connected' if body.enabled else 'disconnected'}")
    return entry
