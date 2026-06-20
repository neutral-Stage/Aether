#!/usr/bin/env python3
"""Validate all knowledge packs (Phase 11, §6.9).

Usage:
  python scripts/validate_packs.py
  make validate-packs
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.knowledge.loader import PACKS_DIR, sideload_dir  # noqa: E402
from aether.knowledge.validator import validate_pack_directory  # noqa: E402


def main() -> int:
    errors: dict[str, list[str]] = {}
    errors.update(validate_pack_directory(PACKS_DIR))

    extra = sideload_dir()
    if extra and extra.exists() and extra != PACKS_DIR:
        for path, errs in validate_pack_directory(extra).items():
            errors[f"sideload:{path}"] = errs

    if not errors:
        builtin = len(list(PACKS_DIR.glob("*.yaml")))
        sideload_count = len(list(extra.glob("*.yaml"))) if extra and extra.exists() else 0
        print(f"OK: {builtin} built-in packs validated", end="")
        if sideload_count:
            print(f", {sideload_count} sideload packs validated", end="")
        print()
        return 0

    for path, errs in sorted(errors.items()):
        for err in errs:
            print(f"ERROR: {err}", file=sys.stderr)
    print(f"\n{len(errors)} pack(s) failed validation", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
