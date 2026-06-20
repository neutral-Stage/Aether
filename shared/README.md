# Shared contracts (Phase 3)

Language-agnostic artifacts used by both the Python sidecar and the Swift shell.

## Tool schemas (`tool_schemas/`)

Regenerate after changing `aether/tools/registry.py`:

```bash
python scripts/export_tool_schemas.py
```

- `manifest.json` — index of all tools
- `<tool_name>.json` — `{ name, description, input_schema, permission, impact }`

## Prompts (`prompts/`)

- `system.txt` — base orchestrator system prompt (mirrors `BASE_SYSTEM_PROMPT` in `aether/core/orchestrator.py`)
