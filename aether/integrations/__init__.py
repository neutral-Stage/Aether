"""Catalog and on/off state for the Apple-app integrations (Calendar, Reminders,
Contacts, Notes, Mail). No accounts or tokens: Calendar/Reminders/Contacts are
read through the Swift app's EventKit/Contacts access (see
``aether/ipc/native_effector.py``); Notes/Mail are read through AppleScript
(``aether/integrations/apple.py``). Every integration defaults to off, and
turning one on never runs anything by itself — see ``aether/tools/
integration_tools.py`` for what each exposes to the model, and
``aether/core/policy.py`` / ``aether/core/drafts.py`` for the confirmation and
editable-draft path the write actions go through.

State is a small JSON file next to the other per-machine prefs
(``<data dir>/integrations.json``), not config.yaml, so the menu bar's
Integrations panel can flip it without a sidecar restart.
"""
from __future__ import annotations

import json
from typing import Any

from ..core.paths import data_dir

STATE_NAME = "integrations.json"

CATALOG: list[dict[str, Any]] = [
    {
        "id": "calendar",
        "name": "Calendar",
        "description": "Read your events and, with your confirmation, add new ones.",
        "tools": ["calendar_events", "calendar_create_event"],
        "kind": "eventkit",
    },
    {
        "id": "reminders",
        "name": "Reminders",
        "description": "Read your reminders and, with your confirmation, add new ones.",
        "tools": ["reminders_list", "reminders_add"],
        "kind": "eventkit",
    },
    {
        "id": "contacts",
        "name": "Contacts",
        "description": "Look up a contact's phone number, email or organization.",
        "tools": ["contacts_search"],
        "kind": "contacts",
    },
    {
        "id": "notes",
        "name": "Notes",
        "description": "Search your notes and, with your confirmation, create new ones.",
        "tools": ["notes_search", "notes_create"],
        "kind": "applescript",
    },
    {
        "id": "mail",
        "name": "Mail",
        "description": "Search your inbox for a message.",
        "tools": ["mail_search"],
        "kind": "applescript",
    },
]

_IDS = frozenset(entry["id"] for entry in CATALOG)


def _state_path():  # noqa: ANN202
    return data_dir() / STATE_NAME


def _load_state() -> dict[str, bool]:
    """The persisted on/off flags, or ``{}`` (all off) when there are none yet
    or the file is missing/corrupt — tolerant by design, never raises."""
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {str(k): bool(v) for k, v in data.items()}
    except (OSError, ValueError, AttributeError, TypeError):
        return {}


def _save_state(state: dict[str, bool]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def enabled(integration_id: str) -> bool:
    """True only when the user has explicitly turned this integration on."""
    return bool(_load_state().get(str(integration_id), False))


def set_enabled(integration_id: str, on: bool) -> dict[str, Any]:
    """Set the flag for ``integration_id``; returns its catalog entry (with
    ``enabled``), or ``{}`` for an id that isn't in the catalog."""
    if integration_id not in _IDS:
        return {}
    state = _load_state()
    state[integration_id] = bool(on)
    _save_state(state)
    entry = next(e for e in CATALOG if e["id"] == integration_id)
    return {**entry, "enabled": bool(on)}


def catalog_status() -> list[dict[str, Any]]:
    """The full catalog with each entry's current ``enabled`` flag."""
    state = _load_state()
    return [{**entry, "enabled": bool(state.get(entry["id"], False))} for entry in CATALOG]
