"""App Knowledge Pack loader (§6.9)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PACKS_DIR = Path(__file__).resolve().parent / "packs"
_DEFAULT_SIDELOAD = Path.home() / ".aether" / "packs"

# Normalize frontmost app names to pack keys.
_APP_ALIASES: dict[str, str] = {
    "mail": "mail",
    "safari": "safari",
    "finder": "finder",
    "davinci resolve": "davinci_resolve",
    "resolve": "davinci_resolve",
    "logic pro": "logic_pro",
    "logic": "logic_pro",
    "microsoft word": "office",
    "microsoft excel": "office",
    "microsoft powerpoint": "office",
    "microsoft outlook": "office",
    "word": "office",
    "excel": "office",
    "powerpoint": "office",
    "outlook": "office",
    "visual studio code": "vscode",
    "code": "vscode",
    "cursor": "vscode",
    # Phase 5 packs
    "slack": "slack",
    "google chrome": "chrome",
    "chrome": "chrome",
    "terminal": "terminal",
    "iterm": "terminal",
    "iterm2": "terminal",
    "notes": "notes",
    "calendar": "calendar",
    # Phase 9 packs
    "figma": "figma",
    "notion": "notion",
    "zoom": "zoom",
    "spotify": "spotify",
    "xcode": "xcode",
}

# Bundle ID → pack key (loaded from pack YAML `bundle_ids` + static map)
_BUNDLE_TO_PACK: dict[str, str] = {
    "com.apple.mail": "mail",
    "com.apple.Safari": "safari",
    "com.apple.finder": "finder",
    "com.tinyspeck.slackmacgap": "slack",
    "com.google.Chrome": "chrome",
    "com.google.Chrome.canary": "chrome",
    "com.apple.Terminal": "terminal",
    "com.googlecode.iterm2": "terminal",
    "com.apple.Notes": "notes",
    "com.apple.iCal": "calendar",
    "com.figma.Desktop": "figma",
    "notion.id": "notion",
    "us.zoom.xos": "zoom",
    "com.spotify.client": "spotify",
    "com.apple.dt.Xcode": "xcode",
}


def sideload_dir() -> Path:
    """User sideload directory from config or default ~/.aether/packs."""
    try:
        from ..core.config import load_config

        cfg = load_config(validate=False)
        raw = cfg.get("knowledge", "sideload_dir")
        if raw:
            return Path(str(raw)).expanduser()
    except Exception:  # noqa: BLE001
        pass
    env = os.getenv("AETHER_PACKS_DIR")
    if env:
        return Path(env).expanduser()
    return _DEFAULT_SIDELOAD


def _pack_search_dirs() -> list[Path]:
    dirs = [PACKS_DIR]
    extra = sideload_dir()
    if extra.exists() and extra not in dirs:
        dirs.append(extra)
    return dirs


def _pack_path(app_key: str) -> Path | None:
    for directory in _pack_search_dirs():
        path = directory / f"{app_key}.yaml"
        if path.exists():
            return path
    return None


@lru_cache(maxsize=32)
def _load_pack_file(app_key: str) -> dict[str, Any] | None:
    path = _pack_path(app_key)
    if path is None:
        return None
    data = yaml.safe_load(path.read_text()) or {}
    for bid in data.get("bundle_ids") or []:
        _BUNDLE_TO_PACK[str(bid)] = app_key
    for alias in data.get("aliases") or []:
        _APP_ALIASES[str(alias).lower()] = app_key
    return data


def list_packs() -> list[str]:
    keys: set[str] = set()
    for directory in _pack_search_dirs():
        if directory.exists():
            keys.update(p.stem for p in directory.glob("*.yaml"))
    return sorted(keys)


def resolve_pack_key(app_name: str = "", bundle_id: str = "") -> str | None:
    """Resolve pack key from display name and/or bundle ID."""
    if bundle_id:
        key = _BUNDLE_TO_PACK.get(bundle_id.strip())
        if key:
            return key
    name = (app_name or "").strip().lower()
    if not name:
        return None
    if name in _APP_ALIASES:
        return _APP_ALIASES[name]
    # Partial match on aliases
    for alias, key in _APP_ALIASES.items():
        if alias in name or name in alias:
            return key
    return None


def load_pack(app_name: str = "", bundle_id: str = "") -> dict[str, Any] | None:
    key = resolve_pack_key(app_name, bundle_id)
    if not key:
        return None
    return _load_pack_file(key)


def prompt_slice(
    app_name: str,
    task_hint: str = "",
    bundle_id: str = "",
) -> str:
    """Return markdown context to inject when the given app is frontmost."""
    pack = load_pack(app_name, bundle_id)
    if not pack:
        return ""
    tier = pack.get("tier", "unknown")
    tier_note = {
        0: "Tier 0 — specialist CLI delegation",
        1: "Tier 1 — AppleScript / Apple Events (preferred)",
        2: "Tier 2 — AX + keyboard shortcuts",
        3: "Tier 3 — vision/OCR fallback",
    }.get(tier, f"Tier {tier}")
    lines = [
        f"## App knowledge: {pack.get('app', app_name)}",
        f"Integration: {tier_note}",
    ]
    shortcuts = pack.get("shortcuts") or []
    if shortcuts:
        lines.append("Key shortcuts:")
        for s in shortcuts[:12]:
            lines.append(f"- {s}")
    recipes = pack.get("recipes") or {}
    if task_hint:
        hint = task_hint.lower()
        for name, steps in recipes.items():
            if any(w in hint for w in name.lower().split()):
                lines.append(f"Recipe ({name}):")
                for step in steps:
                    lines.append(f"  {step}")
                break
    gotchas = pack.get("gotchas") or []
    if gotchas:
        lines.append("Gotchas:")
        for g in gotchas[:6]:
            lines.append(f"- {g}")
    snippets = pack.get("scripting") or {}
    if snippets:
        lines.append(
            "Prefer these scripting tools when applicable: "
            + ", ".join(snippets.keys())
        )
    return "\n".join(lines)
