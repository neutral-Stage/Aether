#!/usr/bin/env python3
"""Ping configured LLM providers with a minimal prompt.

Usage:
  python scripts/test_providers.py              # all providers with keys set
  python scripts/test_providers.py --dry-run    # list providers, no network
  python scripts/test_providers.py --provider openrouter
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from aether.core.providers import collect_api_keys, create_client, merge_role_config
from aether.core.router import RouterConfig


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Aether LLM provider connectivity")
    parser.add_argument("--dry-run", action="store_true", help="List providers only")
    parser.add_argument("--provider", help="Test a single provider template name")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    cfg = RouterConfig.load()
    api_keys = collect_api_keys()
    providers = dict(cfg.raw.get("providers") or {})

    if args.provider:
        if args.provider not in providers:
            print(f"Unknown provider: {args.provider}")
            print(f"Available: {', '.join(sorted(providers))}")
            return 1
        providers = {args.provider: providers[args.provider]}

    if not providers:
        print("No providers defined in configs/router.yaml")
        return 1

    failures = 0
    for name, pcfg in sorted(providers.items()):
        merged = dict(pcfg)
        env = merged.get("api_key_env", "")
        has_key = bool(api_keys.get(str(env)) if env else False)
        backend = merged.get("backend", "?")
        model = merged.get("model", "?")
        print(f"\n[{name}] backend={backend} model={model} key={env} set={has_key}")
        if args.dry_run:
            continue
        if backend == "http":
            print("  skip (local HTTP — use Ollama health check separately)")
            continue
        if not has_key:
            print("  skip (no API key)")
            continue
        client = create_client(merged, api_keys=api_keys, role_name=name)
        if client is None:
            print("  FAIL: could not create client")
            failures += 1
            continue
        try:
            resp = client.step(
                "You are a connectivity test.",
                [{"role": "user", "content": "Reply with exactly: pong"}],
                [],
            )
            snippet = (resp.text or "").strip()[:80]
            print(f"  OK backend={resp.backend} reply={snippet!r}")
        except Exception as e:
            print(f"  FAIL: {e}")
            failures += 1

    # Also report active cloud_frontier role
    for role_name in ("cloud_frontier", "vision"):
        role = merge_role_config(cfg.raw, role_name)
        print(
            f"\nActive {role_name}: provider={role.get('provider', 'inline')} "
            f"backend={role.get('backend')} model={role.get('model')}"
        )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
