"""Example Aether plugin — registers one read-only tool."""
from __future__ import annotations

from aether.tools.registry import Registry, ToolSpec


def register(registry: Registry) -> None:
    def _handler(args: dict, ctx) -> str:
        name = str(args.get("name", "world"))
        return f"Hello, {name}! (from example_hello plugin)"

    registry.register(
        ToolSpec(
            name="plugin_hello",
            description="Say hello from the example plugin (read-only demo).",
            json_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name to greet"},
                },
            },
            permission="none",
            impact="read",
            handler=_handler,
        ),
    )
