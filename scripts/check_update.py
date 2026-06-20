#!/usr/bin/env python3
"""Check local Aether version against a release feed (Phase 5)."""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_local_version() -> str:
    vf = ROOT / "VERSION"
    if vf.exists():
        return vf.read_text(encoding="utf-8").strip()
    return "0.0.0"


def parse_parts(v: str) -> list[int]:
    parts: list[int] = []
    for piece in v.replace("-", ".").split("."):
        digits = "".join(c for c in piece if c.isdigit())
        if digits:
            parts.append(int(digits))
    return parts or [0]


def is_newer(remote: str, local: str) -> bool:
    r, l = parse_parts(remote), parse_parts(local)
    n = max(len(r), len(l))
    for i in range(n):
        rv = r[i] if i < len(r) else 0
        lv = l[i] if i < len(l) else 0
        if rv > lv:
            return True
        if rv < lv:
            return False
    return False


def main() -> int:
    local = read_local_version()
    url = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "https://api.github.com/repos/YOUR_ORG/aether/releases/latest"
    )
    print(f"Local version: {local}")
    print(f"Checking: {url}")
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"Update check failed: {exc}")
        return 1
    tag = str(data.get("tag_name", "")).lstrip("vV")
    if is_newer(tag, local):
        print(f"Update available: {tag}")
        print(f"Release: {data.get('html_url', '')}")
        return 2
    print("Up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
