"""Screen-memory preferences set from outside config.yaml — today, just which
normally-skipped browsers (Safari, Arc, Opera, Orion, DuckDuckGo — see
``browsers.py``) the owner has explicitly allowed. Kept in their own small
JSON file, next to the database, so the menu bar's Safari toggle doesn't
require editing config.yaml or restarting the sidecar, and so hints (which
run even when screen memory itself is off) still see the choice.
"""
from __future__ import annotations

import json
from typing import Any

from ..core.paths import data_dir

PREFS_NAME = "screen_memory_prefs.json"


def _prefs_path():  # noqa: ANN202
    return data_dir() / PREFS_NAME


def load_prefs() -> dict[str, Any]:
    """The persisted prefs, or ``{}`` when there are none yet or the file is bad."""
    try:
        data = json.loads(_prefs_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def save_prefs(prefs: dict[str, Any]) -> None:
    path = _prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prefs), encoding="utf-8")


def allowed_browsers() -> list[str]:
    """The bundle IDs the owner has allowed, sorted."""
    return sorted({str(b) for b in load_prefs().get("allow_browsers") or [] if b})


def set_browser_allowed(bundle_id: str, allowed: bool) -> list[str]:
    """Add or remove ``bundle_id`` from the allowed set; returns the new set."""
    prefs = load_prefs()
    current = {str(b) for b in prefs.get("allow_browsers") or [] if b}
    if allowed:
        current.add(bundle_id)
    else:
        current.discard(bundle_id)
    prefs["allow_browsers"] = sorted(current)
    save_prefs(prefs)
    return prefs["allow_browsers"]
