"""Aether plugin SDK (Phase 12)."""

from .loader import discover_plugin_dirs, load_plugin, load_plugins, plugins_explicitly_enabled

__all__ = ["discover_plugin_dirs", "load_plugins", "load_plugin", "plugins_explicitly_enabled"]
