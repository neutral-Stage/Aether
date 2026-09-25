#!/usr/bin/env python3
"""Export language-agnostic tool schemas from the Python registry to shared/tool_schemas/."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Register every optional tool regardless of the local config.yaml, so the
# exported contract is deterministic (the CI drift check compares it).
os.environ["AETHER_SCHEMA_EXPORT"] = "1"

from aether.tools.registry import DEFAULT_REGISTRY  # noqa: E402

OUT = ROOT / "shared" / "tool_schemas"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    specs = DEFAULT_REGISTRY.all_specs()
    # Drop schema files for tools that no longer exist.
    current = {f"{spec.name}.json" for spec in specs} | {"manifest.json"}
    for stale in OUT.glob("*.json"):
        if stale.name not in current:
            stale.unlink()
    manifest: list[dict] = []

    for spec in specs:
        payload = {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.json_schema,
            "permission": spec.permission,
            "impact": spec.impact,
        }
        path = OUT / f"{spec.name}.json"
        path.write_text(json.dumps(payload, indent=2) + "\n")
        manifest.append({"name": spec.name, "file": path.name})

    manifest_path = OUT / "manifest.json"
    manifest_path.write_text(
        json.dumps({"version": 1, "tool_count": len(manifest), "tools": manifest}, indent=2)
        + "\n"
    )
    print(f"Exported {len(manifest)} tool schemas to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
