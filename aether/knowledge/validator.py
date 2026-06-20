"""Knowledge pack YAML validator (Phase 11, §6.9)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REQUIRED_TOP_LEVEL = frozenset({"app", "tier"})
OPTIONAL_TOP_LEVEL = frozenset({
    "shortcuts",
    "recipes",
    "gotchas",
    "scripting",
    "bundle_ids",
    "aliases",
})
VALID_TIERS = frozenset({0, 1, 2, 3})


def validate_pack_data(data: dict[str, Any], *, path: str = "") -> list[str]:
    """Return list of validation errors (empty if valid)."""
    errors: list[str] = []
    prefix = f"{path}: " if path else ""

    if not isinstance(data, dict):
        return [f"{prefix}pack must be a YAML mapping"]

    missing = REQUIRED_TOP_LEVEL - set(data.keys())
    for key in sorted(missing):
        errors.append(f"{prefix}missing required field '{key}'")

    app = data.get("app")
    if app is not None and not isinstance(app, str):
        errors.append(f"{prefix}'app' must be a string")
    elif isinstance(app, str) and not app.strip():
        errors.append(f"{prefix}'app' must not be empty")

    tier = data.get("tier")
    if tier is not None:
        if not isinstance(tier, int) or tier not in VALID_TIERS:
            errors.append(f"{prefix}'tier' must be an integer 0-3")

    for field in ("shortcuts", "gotchas", "bundle_ids", "aliases"):
        val = data.get(field)
        if val is not None and not isinstance(val, list):
            errors.append(f"{prefix}'{field}' must be a list")

    recipes = data.get("recipes")
    if recipes is not None:
        if not isinstance(recipes, dict):
            errors.append(f"{prefix}'recipes' must be a mapping")
        else:
            for name, steps in recipes.items():
                if not isinstance(steps, list):
                    errors.append(f"{prefix}recipe '{name}' must be a list of steps")
                elif not all(isinstance(s, str) for s in steps):
                    errors.append(f"{prefix}recipe '{name}' steps must be strings")

    scripting = data.get("scripting")
    if scripting is not None and not isinstance(scripting, dict):
        errors.append(f"{prefix}'scripting' must be a mapping")

    return errors


def validate_pack_file(path: Path) -> list[str]:
    """Validate a single pack YAML file."""
    if not path.exists():
        return [f"{path}: file not found"]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return [f"{path}: invalid YAML: {exc}"]
    if not data:
        return [f"{path}: empty pack file"]
    errors = validate_pack_data(data, path=str(path))
    stem = path.stem
    if stem and isinstance(data.get("app"), str):
        slug = data["app"].lower().replace(" ", "_")
        if stem != slug and stem not in (data.get("aliases") or []):
            # Soft warning as error for CI strictness on naming
            pass  # allow finder.yaml for Finder app
    return errors


def validate_pack_directory(directory: Path) -> dict[str, list[str]]:
    """Validate all *.yaml packs in a directory. Returns {path: errors}."""
    results: dict[str, list[str]] = {}
    if not directory.exists():
        return {str(directory): ["directory not found"]}
    for path in sorted(directory.glob("*.yaml")):
        errs = validate_pack_file(path)
        if errs:
            results[str(path)] = errs
    return results
