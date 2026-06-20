"""Backward-compatible tool facade — delegates to aether.tools.registry."""
from __future__ import annotations

from ..effectors import shell as shell_fx
from .registry import (
    AgentContext,
    DEFAULT_REGISTRY,
    Registry,
    ToolSpec,
    build_default_registry,
)

TOOLS: list[dict] = DEFAULT_REGISTRY.schemas()
DESTRUCTIVE_TOOLS = {"run_shell"}


def is_destructive(name: str, args: dict) -> bool:
    spec = DEFAULT_REGISTRY.get(name)
    if spec and spec.impact == "destructive":
        return True
    if name == "run_shell":
        return shell_fx.is_destructive(args.get("command", ""))
    return False


def describe_call(name: str, args: dict) -> str:
    return DEFAULT_REGISTRY.describe_call(name, args)


def execute(name: str, args: dict, ctx: AgentContext) -> str:
    return DEFAULT_REGISTRY.dispatch(name, args, ctx)


__all__ = [
    "AgentContext",
    "Registry",
    "ToolSpec",
    "TOOLS",
    "DESTRUCTIVE_TOOLS",
    "DEFAULT_REGISTRY",
    "build_default_registry",
    "is_destructive",
    "describe_call",
    "execute",
]
