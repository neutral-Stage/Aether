# Aether Plugin SDK

**Version:** 1.0.0-rc.1 · **Status:** Minimal MVP (Phase 12)

How to extend Aether with custom tools via Python plugins.

---

## Overview

Plugins are **directories** with:

- `plugin.yaml` — manifest (name, version, enabled)
- `register.py` — exports `register(registry: Registry)`

Discovery paths (in order):

1. `~/.aether/plugins/<name>/`
2. `<project>/plugins/<name>/`

Loading is **opt-in**:

```yaml
# config.yaml
beta:
  plugins_enabled: true   # or plugins.enabled: true
```

---

## Quick start

Copy the example:

```bash
cp -r plugins/example_hello ~/.aether/plugins/example_hello
```

Enable in `config.yaml`, restart sidecar. The tool `plugin_hello` appears in the registry.

---

## `plugin.yaml`

```yaml
name: my_plugin
version: 0.1.0
description: My custom tools
enabled: true
author: You
```

---

## `register.py`

```python
from aether.tools.registry import Registry, ToolSpec

def register(registry: Registry) -> None:
    def _handler(args: dict, ctx) -> str:
        return f"Result: {args.get('query', '')}"

    registry.register(ToolSpec(
        name="my_tool",
        description="Read-only example tool",
        json_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
        },
        permission="none",      # none | screen | input | shell | files | network
        impact="read",          # read | reversible | destructive
        handler=_handler,
    ))
```

---

## Tool registration rules

| Field | Guidance |
|-------|----------|
| `permission` | Request minimum capability; respect `capabilities.*` in config |
| `impact` | `destructive` triggers confirmation in careful mode |
| `handler` | Must be fast; check STOP via registry dispatch (automatic) |

Export schemas after adding tools:

```bash
python scripts/export_tool_schemas.py
```

---

## Knowledge packs

App-specific hints live in `aether/knowledge/packs/*.yaml`.  
User sideload: `knowledge.sideload_dir` (default `~/.aether/packs`).  
Validate: `make validate-packs`.

Plugins may document pack paths in `plugin.yaml` but packs are separate YAML files today.

---

## MCP servers

External tools via MCP (stdio or SSE) are configured in `config.yaml` → `mcp.servers`.  
See `aether/tools/mcp_client.py` and Phase 10 docs — not loaded via the plugin loader.

---

## Testing

```bash
python -m pytest tests/unit/test_plugin_loader.py -q
```

---

## Security

Plugins run **in-process** with full sidecar privileges. Only install plugins from trusted sources. Keep `plugins_enabled: false` unless needed.

---

*Future: npm-style marketplace, signed plugins, Swift extensions.*
