"""Learned knowledge — pack write-back from successful runs (Phase 10).

Static YAML packs teach Aether an app's shortcuts/recipes; this turns usage into
expertise. When a run succeeds in an app, the tool sequence is distilled into a
named recipe and appended to ``<sideload>/learned/<app_key>.yaml``. The loader
merges these into the pack context, so next time that app is frontmost the model
sees "recipes you've done before" alongside the bundled knowledge.

Kept separate from bundled packs (which sideload would otherwise shadow, not
merge) and from the skill store (which learns cross-app parameterized macros).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from . import loader

MAX_LEARNED_RECIPES = 20      # per app, newest kept
MAX_STEPS = 12                # per recipe


def _learned_dir() -> Path:
    return loader.sideload_dir() / "learned"


def _learned_path(app_key: str) -> Path:
    return _learned_dir() / f"{app_key}.yaml"


def load_learned(app_key: str) -> dict[str, Any]:
    path = _learned_path(app_key)
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _recipe_name(task: str) -> str:
    words = [w for w in "".join(c if c.isalnum() else " " for c in task).split()][:4]
    return "_".join(w.lower() for w in words) or "task"


def record_success(app_key: str, task: str, steps: list[str]) -> str | None:
    """Append a learned recipe for an app. Returns the recipe name, or None when
    there's nothing worth recording. Best-effort — never raises into the caller."""
    steps = [s for s in (steps or []) if s][:MAX_STEPS]
    if not app_key or not steps or len(steps) < 2:
        return None  # single-step "recipes" aren't worth learning
    name = _recipe_name(task)
    try:
        data = load_learned(app_key)
        recipes: dict[str, Any] = data.get("recipes") or {}
        if recipes.get(name) == steps:
            return None  # already known, identical
        recipes[name] = steps
        # keep newest MAX_LEARNED_RECIPES (dict preserves insertion order)
        if len(recipes) > MAX_LEARNED_RECIPES:
            for old in list(recipes)[: len(recipes) - MAX_LEARNED_RECIPES]:
                recipes.pop(old, None)
        data["recipes"] = recipes
        data["app_key"] = app_key
        path = _learned_path(app_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        # bust the loader cache so the new recipe shows up immediately
        loader._load_pack_file.cache_clear()
        return name
    except (OSError, yaml.YAMLError):
        return None


def learned_prompt_slice(app_key: str) -> str:
    """Markdown block of recipes learned from prior successful runs in this app."""
    data = load_learned(app_key)
    recipes = data.get("recipes") or {}
    if not recipes:
        return ""
    lines = ["Learned from your past successful runs:"]
    for name, steps in list(recipes.items())[-6:]:
        lines.append(f"- {name}: " + " → ".join(str(s) for s in steps[:6]))
    return "\n".join(lines)
