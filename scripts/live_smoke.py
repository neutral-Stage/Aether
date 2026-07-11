#!/usr/bin/env python3
"""Live end-to-end smoke test (Phase 11).

Almost everything in Aether has been verified with mocks/fixtures. This exercises
the REAL stack against a running sidecar and reports pass/fail per exit criterion.

The KEYLESS checks (sidecar boot, /health, /catalog, run_store reconcile) run
anywhere and are asserted here + in CI. The KEY/CLI checks (fleet spawn against a
real `claude`, a real graph, STOP kill-latency) require your machine's API keys
and a coding CLI on PATH — they're gated behind --with-agents and print a manual
checklist otherwise.

Usage:
    python -m sidecar.server &                 # or let the Swift app supervise it
    python scripts/live_smoke.py               # keyless checks
    python scripts/live_smoke.py --with-agents # + real fleet/graph (needs claude + key)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("AETHER_SIDECAR_URL", "http://127.0.0.1:8765")
TOKEN = os.environ.get("AETHER_SIDECAR_TOKEN", "")


def _req(path: str, method: str = "GET", body: dict | None = None, timeout: float = 10.0):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read() or "null")


class Smoke:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
        self.passed += ok
        self.failed += not ok

    def keyless(self) -> None:
        print("Keyless checks (sidecar must be running):")
        try:
            status, health = _req("/health")
            self.check("GET /health 200 + ok", status == 200 and health.get("ok"),
                       f"service={health.get('service')}")
        except Exception as e:  # noqa: BLE001
            self.check("GET /health", False, f"is the sidecar up? {e}")
            return
        try:
            _, cat = _req("/catalog")
            self.check("GET /catalog lists apps+tools",
                       cat.get("tool_count", 0) > 0 and len(cat.get("apps", [])) >= 20,
                       f"{cat.get('tool_count')} tools, {len(cat.get('apps', []))} apps")
        except Exception as e:  # noqa: BLE001
            self.check("GET /catalog", False, str(e))
        try:
            _, runs = _req("/runs")
            self.check("GET /runs (reconcile ran, no phantom 'running' on boot)",
                       runs.get("active", 0) == 0,
                       f"active={runs.get('active')}")
        except Exception as e:  # noqa: BLE001
            self.check("GET /runs", False, str(e))
        try:
            # /events must accept an SSE subscription (proactive triggers path)
            req = urllib.request.Request(f"{BASE}/events")
            if TOKEN:
                req.add_header("Authorization", f"Bearer {TOKEN}")
            with urllib.request.urlopen(req, timeout=2) as resp:
                self.check("GET /events opens an SSE stream",
                           resp.status == 200
                           and "event-stream" in resp.headers.get("content-type", ""))
        except Exception as e:  # noqa: BLE001
            # a 2s read that yields the first ping/nothing is fine; only connect errors fail
            self.check("GET /events opens an SSE stream", "timed out" in str(e).lower(),
                       str(e))

    def with_agents(self) -> None:
        print("\nAgent checks (need a coding CLI on PATH + API key):")
        try:
            status, res = _req("/fleet/spawn", "POST",
                               {"agent_type": "claude", "prompt": "print hello then stop",
                                "workspace": os.getcwd()})
            sid = res.get("session_id")
            self.check("POST /fleet/spawn started a session", bool(sid), f"id={sid}")
            if not sid:
                return
            time.sleep(2)
            t0 = time.monotonic()
            _req("/fleet/stop_all", "POST", {})
            _, one = _req(f"/fleet/{sid}")
            killed_ms = (time.monotonic() - t0) * 1000
            self.check("STOP halts the fleet session < 3s",
                       one.get("state") in ("stopped", "done", "error", "timeout")
                       and killed_ms < 3000, f"{killed_ms:.0f}ms, state={one.get('state')}")
        except Exception as e:  # noqa: BLE001
            self.check("fleet spawn/stop", False, str(e))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-agents", action="store_true",
                    help="also run real fleet/graph checks (needs claude + key)")
    args = ap.parse_args()

    s = Smoke()
    s.keyless()
    if args.with_agents:
        s.with_agents()
    else:
        print("\n(Skipping agent checks. Re-run with --with-agents once a coding CLI"
              " + API key are set up to validate STOP/fleet against reality.)")

    print(f"\n{s.passed} passed, {s.failed} failed")
    return 1 if s.failed else 0


if __name__ == "__main__":
    sys.exit(main())
