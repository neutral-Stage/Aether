"""Registry side of self-written tools: ``make_tool`` and the installed ``my_*`` tools.

The agent loop runs ``make_tool`` itself (approval, generation, review) and
runs ``my_*`` tools with automatic repair; the handlers here are the plain
paths for callers outside an agent run.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..toolsmith import executor, store
from ..toolsmith.manifest import PREFIX, ToolManifest, tool_name
from ..toolsmith.settings import Settings

if TYPE_CHECKING:
    from .registry import AgentContext, Registry, ToolSpec

MAKE_TOOL_DESCRIPTION = (
    "Write yourself a new tool when a task needs a repeatable computation or file "
    "transformation that no existing tool does well, e.g. batch-rename files by a pattern, "
    "merge CSV files, extract links from saved HTML, or compute totals from an export. The "
    "user approves what it may access; it then runs in a sandbox and appears as my_<name>. "
    "Prefer existing tools, and do not use this for a one-off shell command.")


def make_tool_spec() -> ToolSpec:
    from .registry import ToolSpec

    dirs = {"type": "array", "items": {"type": "string"}, "maxItems": 5}
    return ToolSpec(
        name="make_tool",
        description=MAKE_TOOL_DESCRIPTION,
        json_schema={"type": "object", "properties": {
            "name": {"type": "string",
                     "description": "short name, e.g. 'merge csv files' (becomes my_merge_csv_files)"},
            "description": {"type": "string",
                            "description": "one sentence the user reads: what the tool does"},
            "params": {"type": "object",
                       "description": ("parameter name → {\"type\": string|number|integer|"
                                       "boolean, \"description\": …}")},
            "required": {"type": "array", "items": {"type": "string"}},
            "how": {"type": "string",
                    "description": "how it should work: inputs, steps and what it returns"},
            "network": {"type": "boolean",
                        "description": "true only if it must reach the internet"},
            "write_dirs": {**dirs, "description": ("folders it writes into (absolute or ~/…); "
                                                   "leave empty if it only returns text")},
            "read_dirs": {**dirs, "description": ("with network only: the folders it needs to "
                                                  "read")},
        }, "required": ["name", "description", "params", "how"]},
        permission="shell", impact="reversible", handler=_h_make_tool_outside_run)


def _h_make_tool_outside_run(_args: dict, _ctx: AgentContext) -> str:
    return "ERROR: make_tool is only available inside an agent run (it needs the user's approval)."


def manifest_from_args(args: dict) -> ToolManifest:
    params = args.get("params")
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except ValueError:
            params = {}
    clean: dict[str, dict[str, str]] = {}
    for key, spec in (params or {}).items() if isinstance(params, dict) else []:
        if isinstance(spec, str):          # {"path": "string"}
            spec = {"type": spec}
        if isinstance(spec, dict):
            clean[str(key)] = {"type": str(spec.get("type", "string")),
                               "description": str(spec.get("description", ""))[:200]}
    required = args.get("required")
    if not isinstance(required, list):
        required = list(clean)
    return ToolManifest(
        name=tool_name(str(args.get("name") or "")),
        description=" ".join(str(args.get("description") or "").split())[:400],
        params=clean, required=[str(r) for r in required],
        network=bool(args.get("network", False)),
        write_dirs=[str(d) for d in args.get("write_dirs") or [] if str(d).strip()],
        read_dirs=[str(d) for d in args.get("read_dirs") or [] if str(d).strip()],
        how=str(args.get("how") or "").strip()[:2000])


def approval_text(m: ToolManifest, *, replacing: bool) -> str:
    inputs = ", ".join(f"{k} ({v.get('type', 'string')})" for k, v in m.params.items()) or "none"
    head = (f"🛠 {'Replace your tool' if replacing else 'Create a new tool'}: {m.name}"
            f"\nWhat it does: {m.description}\nInputs: {inputs}\nIt:")
    body = "\n".join(f"  • {line}" for line in m.capability_lines())
    return (f"{head}\n{body}\nAether writes the code, a separate review checks it, and "
            "every run is sandboxed to exactly this.")


def spec_for(tool: store.InstalledTool, settings: Settings | None = None) -> ToolSpec:
    from .registry import ToolSpec

    m = tool.manifest

    def handler(args: dict, _ctx: AgentContext) -> str:
        current = store.load(m.name)
        if current is None:
            return f"ERROR: {m.name} is not installed any more."
        result = executor.run_tool(current.manifest, current.code(), args,
                                   settings or Settings.load())
        return result.text(m.name)

    return ToolSpec(name=m.name,
                    description=f"{m.description} (a tool you wrote; {m.capability_text()})",
                    json_schema=m.json_schema(), permission="shell", impact="reversible",
                    handler=handler)


def sync_registry(registry: Registry, settings: Settings | None) -> list[str]:
    """Register the installed tools (none when ``settings`` is None or disabled)."""
    wanted = {t.name: t for t in store.list_tools()} if settings and settings.enabled else {}
    for spec in registry.all_specs():
        if spec.name.startswith(PREFIX) and spec.name not in wanted:
            registry.unregister(spec.name)
    for tool in wanted.values():
        registry.register(spec_for(tool, settings))
    return sorted(wanted)


def describe(name: str, args: dict) -> str | None:
    if name == "make_tool":
        return f"write a new tool: {tool_name(str(args.get('name') or ''))}"
    if name.startswith(PREFIX):
        return f"run my tool {name}"
    return None
