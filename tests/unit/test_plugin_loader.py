"""Plugin loader tests (Phase 12)."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aether.plugins.loader import discover_plugin_dirs, load_plugin, load_plugins
from aether.tools.registry import AgentContext, Registry


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    root = tmp_path / "plugins" / "demo"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        "name: demo\nenabled: true\n",
        encoding="utf-8",
    )
    (root / "register.py").write_text(
        textwrap.dedent(
            '''
            from aether.tools.registry import ToolSpec

            def register(registry):
                registry.register(ToolSpec(
                    name="plugin_demo",
                    description="demo",
                    json_schema={"type": "object", "properties": {}},
                    permission="none",
                    impact="read",
                    handler=lambda _a, _c: "demo-ok",
                ))
            '''
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return root


class TestPluginLoader:
    def test_discover_plugin_dirs(self, plugin_dir: Path, tmp_path: Path) -> None:
        found = discover_plugin_dirs(project_root=tmp_path)
        assert plugin_dir in found

    def test_load_plugin_registers_tool(self, plugin_dir: Path) -> None:
        reg = Registry()
        name = load_plugin(plugin_dir, reg)
        assert name == "demo"
        assert reg.get("plugin_demo") is not None
        assert reg.dispatch("plugin_demo", {}, AgentContext()) == "demo-ok"

    def test_disabled_plugin_skipped(self, plugin_dir: Path) -> None:
        (plugin_dir / "plugin.yaml").write_text("name: demo\nenabled: false\n")
        reg = Registry()
        assert load_plugin(plugin_dir, reg) is None

    def test_example_hello_plugin(self) -> None:
        root = Path(__file__).resolve().parents[2] / "plugins" / "example_hello"
        if not (root / "plugin.yaml").exists():
            pytest.skip("example plugin not present")
        reg = Registry()
        load_plugin(root, reg)
        out = reg.dispatch("plugin_hello", {"name": "Aether"}, AgentContext())
        assert "Hello, Aether" in out

    def test_load_plugins_from_project(self, plugin_dir: Path, tmp_path: Path) -> None:
        reg = Registry()
        cfg = {"plugins": {"enabled": True, "require_explicit_enable": True}, "beta": {}}
        loaded = load_plugins(reg, project_root=tmp_path, config=cfg)
        assert "demo" in loaded

    def test_load_plugins_skipped_without_explicit_enable(self, plugin_dir: Path, tmp_path: Path) -> None:
        reg = Registry()
        cfg = {"plugins": {"enabled": False, "require_explicit_enable": True}, "beta": {}}
        loaded = load_plugins(reg, project_root=tmp_path, config=cfg)
        assert loaded == []
