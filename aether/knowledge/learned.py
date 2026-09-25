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
PROVEN_AFTER = 3              # identical successes before a recipe is "proven"


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


def safe_to_learn(steps: list[str]) -> bool:
    """Learned recipes are re-injected into future prompts, so text that looks
    like instructions to an AI never gets in (audit residual 7)."""
    from ..core.security import InjectionSeverity, scan_injection

    scan = scan_injection("\n".join(steps))
    return scan.severity not in (InjectionSeverity.HIGH, InjectionSeverity.MEDIUM)


def record_success(app_key: str, task: str, steps: list[str], *,
                   tainted: bool = False) -> str | None:
    """Append (or re-confirm) a learned recipe for an app. Returns the recipe
    name, or None when there's nothing worth recording. Runs that read
    untrusted content teach nothing. Best-effort — never raises."""
    steps = [s for s in (steps or []) if s][:MAX_STEPS]
    if tainted or not app_key or not steps or len(steps) < 2:
        return None  # single-step "recipes" aren't worth learning
    if not safe_to_learn(steps):
        return None
    name = _recipe_name(task)
    try:
        data = load_learned(app_key)
        recipes: dict[str, Any] = data.get("recipes") or {}
        counts: dict[str, int] = data.get("counts") or {}
        if recipes.get(name) == steps:
            counts[name] = int(counts.get(name, 1)) + 1   # the same way worked again
        else:
            counts[name] = 1
        data["counts"] = counts
        recipes[name] = steps
        # keep newest MAX_LEARNED_RECIPES (dict preserves insertion order)
        if len(recipes) > MAX_LEARNED_RECIPES:
            for old in list(recipes)[: len(recipes) - MAX_LEARNED_RECIPES]:
                recipes.pop(old, None)
                counts.pop(old, None)
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
    counts = data.get("counts") or {}
    proven = [(n, st) for n, st in recipes.items() if int(counts.get(n, 1)) >= PROVEN_AFTER]
    other = [(n, st) for n, st in recipes.items() if int(counts.get(n, 1)) < PROVEN_AFTER]
    lines = ["Learned from your past successful runs:"]
    for name, steps in proven[-4:]:
        lines.append(f"- {name} (proven: worked {counts[name]} times the same way): "
                     + " → ".join(str(s) for s in steps[:8]))
    for name, steps in other[-(6 - min(len(proven), 4)):]:
        lines.append(f"- {name}: " + " → ".join(str(s) for s in steps[:6]))
    return "\n".join(lines)
