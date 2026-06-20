"""Unit tests for tool registry dispatch (Phase 6)."""
from __future__ import annotations

import pytest

from aether.core import stop as stop_ctl
from aether.tools.registry import AgentContext, DEFAULT_REGISTRY, Registry, ToolSpec


@pytest.mark.unit
class TestRegistry:
    def test_default_registry_has_core_tools(self) -> None:
        names = {t.name for t in DEFAULT_REGISTRY.all_specs()}
        for required in (
            "get_screen_context",
            "open_app",
            "click",
            "finish",
            "run_shell",
            "browser_navigate",
        ):
            assert required in names
        assert len(names) >= 19

    def test_dispatch_unknown_tool(self) -> None:
        reg = Registry()
        ctx = AgentContext()
        out = reg.dispatch("nonexistent_tool", {}, ctx)
        assert "Unknown tool" in out

    def test_finish_handler(self) -> None:
        spec = DEFAULT_REGISTRY.get("finish")
        assert spec is not None
        assert spec.handler is not None
        msg = spec.handler({"message": "Done."}, AgentContext())
        assert "Done." in msg

    def test_describe_call_click(self) -> None:
        text = DEFAULT_REGISTRY.describe_call("click", {"element_index": 3})
        assert "3" in text

    def test_schemas_are_anthropic_format(self) -> None:
        schemas = DEFAULT_REGISTRY.schemas()
        assert schemas
        first = schemas[0]
        assert "name" in first
        assert "input_schema" in first

    def test_stop_propagates_from_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = Registry()

        def _boom(_args: dict, _ctx: AgentContext) -> str:
            stop_ctl.check()
            return "ok"

        reg.register(
            ToolSpec(
                name="stop_probe",
                json_schema={"type": "object", "properties": {}},
                permission="none",
                impact="read",
                handler=_boom,
            )
        )
        stop_ctl.trigger("test")
        with pytest.raises(stop_ctl.StopRequested):
            reg.dispatch("stop_probe", {}, AgentContext())
        stop_ctl.reset()
