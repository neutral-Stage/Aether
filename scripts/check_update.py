#!/usr/bin/env python3
"""Check local Aether version against a GitHub releases feed (Phase 5)."""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "neutral-Stage/Aether"


def read_local_version() -> str:
    vf = ROOT / "VERSION"
    if vf.exists():
        return vf.read_text(encoding="utf-8").strip()
    return "0.0.0"


def parse_semver(v: str) -> tuple[list[int], list[int]]:
    clean = v.strip().lstrip("vV")
    core_str, _, pre_str = clean.partition("-")
    core = [int(p) for p in core_str.split(".") if p.isdigit()] or [0]
    if not pre_str:
        return core, []
    pre: list[int] = []
    for piece in pre_str.split("."):
        digits = "".join(c for c in piece if c.isdigit())
        if digits:
            pre.append(int(digits))
    return core, (pre or [0])


def is_newer(remote: str, local: str) -> bool:
    r_core, r_pre = parse_semver(remote)
    l_core, l_pre = parse_semver(local)
    n = max(len(r_core), len(l_core))
    for i in range(n):
        rv = r_core[i] if i < len(r_core) else 0
        lv = l_core[i] if i < len(l_core) else 0
        if rv > lv:
            return True
        if rv < lv:
            return False
    # Same core version: stable release beats pre-release (e.g. 1.0.0 > 1.0.0-rc.1).
    if not r_pre and l_pre:
        return True
    if r_pre and not l_pre:
        return False
    m = max(len(r_pre), len(l_pre))
    for i in range(m):
        rv = r_pre[i] if i < len(r_pre) else 0
        lv = l_pre[i] if i < len(l_pre) else 0
        if rv > lv:
            return True
        if rv < lv:
            return False
    return False


def resolve_feed_url(arg: str | None = None) -> str:
    if arg:
        return arg
    env_url = os.environ.get("AETHER_UPDATE_FEED_URL", "").strip()
    if env_url:
        return env_url
    try:
        import yaml

        cfg_path = ROOT / "config.yaml"
        if cfg_path.exists():
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            cfg_url = str((raw.get("beta") or {}).get("update_feed_url") or "").strip()
            if cfg_url:
                return cfg_url
    except Exception:  # noqa: BLE001
        pass
    repo = os.environ.get("AETHER_GITHUB_REPO", "").strip() or DEFAULT_REPO
    return f"https://api.github.com/repos/{repo}/releases/latest"


def main() -> int:
    local = read_local_version()
    url = resolve_feed_url(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"Local version: {local}")
    print(f"Checking: {url}")
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
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
