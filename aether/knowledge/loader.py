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

_prewarmed = False

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
    """User sideload directory from env override, config, or default ~/.aether/packs."""
    env = os.getenv("AETHER_PACKS_DIR")
    if env:
        return Path(env).expanduser()
    try:
        from ..core.config import load_config

        cfg = load_config(validate=False)
        raw = cfg.get("knowledge", "sideload_dir")
        if raw:
            return Path(str(raw)).expanduser()
    except Exception:  # noqa: BLE001
        pass
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


@lru_cache(maxsize=128)
def _load_pack_file(app_key: str) -> dict[str, Any] | None:
    path = _pack_path(app_key)
    if path is None:
        return None
    data = yaml.safe_load(path.read_text()) or {}
    data["_key"] = app_key
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


def catalog_apps() -> list[dict[str, str]]:
    """[{key, app}] for every discoverable pack — powers the in-app 'what can
    I control' surface (GET /catalog)."""
    out: list[dict[str, str]] = []
    for key in list_packs():
        pack = _load_pack_file(key) or {}
        out.append({"key": key, "app": str(pack.get("app", key))})
    return sorted(out, key=lambda d: d["app"].lower())


def prewarm_packs() -> int:
    """Load every available pack so its bundle_ids/aliases self-register."""
    count = 0
    for key in list_packs():
        if _load_pack_file(key) is not None:
            count += 1
    return count


def _ensure_prewarmed() -> None:
    global _prewarmed
    if _prewarmed:
        return
    _prewarmed = True
    try:
        prewarm_packs()
    except Exception:  # noqa: BLE001 — selection must still work if one pack is bad
        pass


def resolve_pack_key(app_name: str = "", bundle_id: str = "") -> str | None:
    """Resolve pack key from display name and/or bundle ID."""
    _ensure_prewarmed()
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
    verified = match_verified_recipe(pack, task_hint) if task_hint else None
    if verified:
        lines.append(render_verified_recipe(key_for_pack(pack), *verified))
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
    # Merge in recipes learned from the user's own successful runs (Phase 10).
    key = resolve_pack_key(app_name, bundle_id)
    if key:
        try:
            from . import learned
            learned_block = learned.learned_prompt_slice(key)
            if learned_block:
                lines.append(learned_block)
        except Exception:  # noqa: BLE001 — learning is additive, never fatal
            pass
    return "\n".join(lines)


# ---- verified recipes (pack schema v2) ---------------------------------------------------
#
#   verified_recipes:
#     create_note:
#       match: ["new note", "create a note", "make a note"]   # key phrases
#       steps:                                                  # exact tool calls
#         - {tool: open_app, args: {name: Notes}}
#         - {tool: press_key, args: {key: n, modifiers: [cmd]}}
#         - {tool: type_text, args: {text: "<the note's text>"}}
#       guide:                                                  # optional, for guide mode
#         - {say: "Open Notes", done_when: app, expect: Notes}
#
# A recipe counts as tested once it passed in the VM benchmark; that result is
# stored in verified.json ({"<pack>.<recipe>": "YYYY-MM-DD"}), not in the pack.

VERIFIED_PATH = Path(__file__).resolve().parent / "verified.json"
_STOP_WORDS = frozenset({"a", "an", "the", "to", "my", "me", "for", "in", "on", "of", "and",
                         "please", "can", "you", "i", "it", "this", "that", "with", "how"})


def _words(text: str) -> list[str]:
    import re

    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP_WORDS]


def key_for_pack(pack: dict[str, Any]) -> str:
    return str(pack.get("_key") or str(pack.get("app", "")).lower().replace(" ", "_"))


def verified_dates() -> dict[str, str]:
    try:
        import json

        data = json.loads(VERIFIED_PATH.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def stamp_verified(refs: list[str], date: str) -> int:
    """Record that recipes passed in the VM benchmark on `date`. Returns how many."""
    import json

    data = verified_dates()
    for ref in refs:
        data[ref] = date
    VERIFIED_PATH.write_text(json.dumps(dict(sorted(data.items())), indent=2) + "\n",
                             encoding="utf-8")
    return len(refs)


def match_verified_recipe(pack: dict[str, Any], goal: str, *, min_words: int = 1
                          ) -> tuple[str, dict[str, Any]] | None:
    """The pack's recipe whose key phrase is fully contained in the goal (the
    longest such phrase wins), or None. Phrases shorter than ``min_words``
    content words are ignored."""
    goal_words = set(_words(goal))
    if not goal_words:
        return None
    best: tuple[int, str, dict[str, Any]] | None = None
    for name, recipe in (pack.get("verified_recipes") or {}).items():
        if not isinstance(recipe, dict):
            continue
        for phrase in recipe.get("match") or []:
            words = _words(str(phrase))
            if len(words) < max(min_words, 1) or not set(words) <= goal_words:
                continue
            if best is None or len(words) > best[0]:
                best = (len(words), str(name), recipe)
    return (best[1], best[2]) if best else None


def verified_recipe_for(goal: str, app_name: str = "", bundle_id: str = ""
                        ) -> tuple[str, str, dict[str, Any]] | None:
    """Best recipe for a goal: the front app's pack first, then every pack.
    Returns (pack key, recipe name, recipe) or None."""
    first = resolve_pack_key(app_name, bundle_id) if (app_name or bundle_id) else None
    keys = ([first] if first else []) + [k for k in list_packs() if k != first]
    best: tuple[int, str, str, dict[str, Any]] | None = None
    for key in keys:
        pack = _load_pack_file(key) or {}
        # Another app's recipe needs a longer phrase to count: less guessing.
        hit = match_verified_recipe(pack, goal, min_words=1 if key == first else 2)
        if not hit:
            continue
        name, recipe = hit
        size = max(len(_words(str(p))) for p in recipe.get("match") or [""])
        if best is None or size > best[0] or (key == first and size == best[0]):
            best = (size, key, name, recipe)
    return (best[1], best[2], best[3]) if best else None


def render_verified_recipe(pack_key: str, name: str, recipe: dict[str, Any]) -> str:
    import json

    date = verified_dates().get(f"{pack_key}.{name}")
    status = f"tested on a Mac on {date}" if date else "not yet tested"
    lines = [f"Recipe for this task ({name}, {status}). Follow these tool calls in order, "
             "filling in <placeholders> from the request; adapt only if the screen differs:"]
    for i, step in enumerate(recipe.get("steps") or [], 1):
        args = json.dumps(step.get("args") or {}, ensure_ascii=False)
        lines.append(f"  {i}. {step.get('tool')} {args}")
    return "\n".join(lines)
