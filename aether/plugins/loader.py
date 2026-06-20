"""Discover and load Aether plugins from user or project directories (Phase 12).

Plugins are directories containing ``plugin.yaml`` and a Python module with
``register(registry)`` hook.

**Security:** Plugins run in-process with full agent privileges (screen, shell,
files). Only enable for trusted code. Set ``beta.plugins_enabled: true`` or
``plugins.enabled: true`` explicitly; ``plugins.require_explicit_enable`` (default
true) blocks discovery when neither flag is set.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

from ..tools.registry import Registry

log = logging.getLogger(__name__)

PLUGIN_MANIFEST = "plugin.yaml"
REGISTER_MODULE = "register.py"


def discover_plugin_dirs(
    *,
    project_root: Path | None = None,
    user_dir: Path | None = None,
) -> list[Path]:
    """Return enabled plugin directories in load order."""
    roots: list[Path] = []
    home = Path.home() / ".aether" / "plugins"
    if user_dir:
        roots.append(user_dir)
    else:
        roots.append(home)
    if project_root:
        roots.append(project_root / "plugins")

    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            manifest = child / PLUGIN_MANIFEST
            if not manifest.exists():
                continue
            name = child.name
            if name in seen:
                continue
            seen.add(name)
            found.append(child)
    return found


def _load_manifest(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid plugin manifest: {path}")
    return data


def _import_register(module_path: Path, plugin_name: str):
    spec = importlib.util.spec_from_file_location(
        f"aether_plugin_{plugin_name}",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load plugin module: {module_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    register = getattr(mod, "register", None)
    if not callable(register):
        raise AttributeError(f"Plugin {plugin_name} missing register(registry) hook")
    return register


def load_plugin(plugin_dir: Path, registry: Registry) -> str | None:
    """Load one plugin directory. Returns plugin name or None if skipped."""
    manifest_path = plugin_dir / PLUGIN_MANIFEST
    if not manifest_path.exists():
        return None
    manifest = _load_manifest(manifest_path)
    if not bool(manifest.get("enabled", True)):
        log.info("Plugin %s disabled in manifest", plugin_dir.name)
        return None
    name = str(manifest.get("name", plugin_dir.name))
    register_path = plugin_dir / REGISTER_MODULE
    if not register_path.exists():
        log.warning("Plugin %s has no %s", name, REGISTER_MODULE)
        return None
    register = _import_register(register_path, name)
    register(registry)
    log.info("Loaded plugin %s from %s", name, plugin_dir)
    return name


def plugins_explicitly_enabled(config: dict[str, Any] | None) -> bool:
    """Return True when config explicitly opts into plugin loading."""
    if config is None:
        return False
    plugins_cfg = config.get("plugins") or {}
    beta = config.get("beta") or {}
    if bool(plugins_cfg.get("require_explicit_enable", True)):
        return bool(plugins_cfg.get("enabled", False)) or bool(beta.get("plugins_enabled", False))
    return True


def load_plugins(
    registry: Registry,
    project_root: Path | None = None,
    *,
    user_dir: Path | None = None,
    config: dict[str, Any] | None = None,
) -> list[str]:
    """Discover and register all enabled plugins. Returns loaded plugin names."""
    if not plugins_explicitly_enabled(config):
        return []
    loaded: list[str] = []
    for plugin_dir in discover_plugin_dirs(project_root=project_root, user_dir=user_dir):
        try:
            name = load_plugin(plugin_dir, registry)
            if name:
                loaded.append(name)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to load plugin %s: %s", plugin_dir.name, exc)
    return loaded
