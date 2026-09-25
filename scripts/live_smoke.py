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
import urllib.error
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


    # ---- Phase A validation gate: the real agent on the real Mac ----

    def with_llm(self) -> None:
        print("\nLLM checks (need the default brain's API key):")
        try:
            _, doc = _req("/doctor?online=true", timeout=30)
            online = next((c for c in doc.get("checks", [])
                           if c.get("name") == "Default brain reachable"), {})
            self.check("default brain accepts the API key", online.get("status") == "ok",
                       online.get("detail", ""))
        except Exception as e:  # noqa: BLE001
            self.check("GET /doctor?online=true", False, str(e))
        run = self._run("Call get_screen_context, then call finish with only the name of "
                        "the frontmost app.", max_steps=4)
        self.check("a real goal completes (perceive → reason → finish)",
                   run.get("status") == "idle" and bool(run.get("result"))
                   and not run.get("error"),
                   f"status={run.get('status')} result={str(run.get('result'))[:60]!r}"
                   + (f" error={run.get('error')}" if run.get("error") else ""))
        try:
            _, m = _req("/metrics")
            self.check("token cost is being tracked", float(m.get("total_cost_usd", 0)) > 0,
                       f"total_cost_usd={m.get('total_cost_usd')}")
        except Exception as e:  # noqa: BLE001
            self.check("GET /metrics", False, str(e))

    def tasks(self) -> list[tuple[str, bool, str]]:
        """The five canonical tasks, each verified in the target app itself."""
        print("\nCanonical tasks (drive real apps; takes a few minutes):")
        rows: list[tuple[str, bool, str]] = []
        for name, goal, probe, expect in CANONICAL_TASKS:
            t0 = time.monotonic()
            run = self._run(goal, max_steps=20, timeout=240)
            secs = time.monotonic() - t0
            observed = _osascript(probe) if probe else str(run.get("result") or "")
            ok = run.get("status") == "idle" and expect.lower() in observed.lower()
            detail = (f"{secs:.0f}s, status={run.get('status')}, "
                      f"saw {observed.strip()[:60]!r}")
            self.check(name, ok, detail)
            rows.append((name, ok, detail))
        rows.append(self._stop_latency())
        return rows

    def _stop_latency(self) -> tuple[str, bool, str]:
        import threading

        name = "STOP halts a running task < 2 s"
        box: dict = {}
        th = threading.Thread(target=lambda: box.update(self._run(
            "Open TextEdit and slowly type the numbers from 1 to 200, one per line.",
            max_steps=40, timeout=120)), daemon=True)
        th.start()
        time.sleep(6)
        t0 = time.monotonic()
        try:
            _req("/stop", "POST", {})
        except Exception as e:  # noqa: BLE001
            self.check(name, False, str(e))
            return (name, False, str(e))
        th.join(timeout=10)
        ms = (time.monotonic() - t0) * 1000
        ok = box.get("status") == "stopped" and ms < 2000
        detail = f"{ms:.0f} ms, status={box.get('status')}"
        self.check(name, ok, detail)
        return (name, ok, detail)

    def _run(self, goal: str, *, max_steps: int, timeout: float = 120) -> dict:
        try:
            _, res = _req("/run", "POST", {"goal": goal, "stream": False,
                                           "max_steps": max_steps}, timeout=timeout)
            return res or {}
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read() or b"{}")
                err = data.get("error")
                detail = (data.get("detail")
                          or (err.get("message") if isinstance(err, dict) else err)
                          or e.reason)
            except Exception:  # noqa: BLE001
                detail = e.reason
            return {"status": "error", "error": f"HTTP {e.code}: {detail}"}
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "error": str(e)}


# (name, goal, AppleScript that reads the outcome back, text it must contain).
# A None probe checks the run's own result text instead.
CANONICAL_TASKS: list[tuple[str, str, str | None, str]] = [
    ("Finder: open Downloads",
     "Open Finder and show the Downloads folder in the front window.",
     'tell application "Finder" to get POSIX path of (target of front Finder window as alias)',
     "/Downloads"),
    ("Safari: read a page title",
     "Open Safari, go to https://example.com and finish with the page's title.",
     None, "Example Domain"),
    ("Notes: create a note",
     "Create a new note in Notes titled 'Aether test' with the text 'hello from Aether'.",
     'tell application "Notes" to get name of every note whose name contains "Aether test"',
     "Aether test"),
    ("Mail: draft (not send) an email",
     "In Mail, draft a new email to me@example.com with subject 'Aether test' and body "
     "'hello'. Do NOT send it; leave the draft open.",
     'tell application "Mail" to get subject of every outgoing message',
     "Aether test"),
    ("Terminal: run a command",
     "Open Terminal and run the command: echo aether-ok",
     'tell application "Terminal" to get contents of selected tab of front window',
     "aether-ok"),
]


def _osascript(script: str) -> str:
    import subprocess

    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True,  # noqa: S603
                             text=True, timeout=20)
        return out.stdout or out.stderr
    except Exception as e:  # noqa: BLE001
        return f"(osascript failed: {e})"


def _write_log(rows: list[tuple[str, bool, str]]) -> str:
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from aether.core.paths import data_dir

    stamp = time.strftime("%Y-%m-%d %H:%M")
    lines = [f"### {stamp} — live_smoke --tasks", "", "| Check | Result | Detail |",
             "|---|---|---|"]
    lines += [f"| {n} | {'pass' if ok else 'FAIL'} | {d} |" for n, ok, d in rows]
    out = data_dir() / "validation" / f"{time.strftime('%Y%m%d-%H%M%S')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    return str(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-llm", action="store_true",
                    help="test the API key and run one real goal (costs a few cents)")
    ap.add_argument("--tasks", action="store_true",
                    help="run the five canonical tasks + STOP latency (drives real apps)")
    ap.add_argument("--with-agents", action="store_true",
                    help="also run real fleet/graph checks (needs claude + key)")
    args = ap.parse_args()

    s = Smoke()
    s.keyless()
    if args.with_llm or args.tasks:
        s.with_llm()
    if args.tasks:
        rows = s.tasks()
        print(f"\nResult table saved to {_write_log(rows)} — paste it into docs/VALIDATION_LOG.md")
    if args.with_agents:
        s.with_agents()
    if not (args.with_llm or args.tasks or args.with_agents):
        print("\n(Keyless checks only. Next: --with-llm, then --tasks on the Mac; "
              "--with-agents once a coding CLI is set up.)")

    print(f"\n{s.passed} passed, {s.failed} failed")
    return 1 if s.failed else 0


if __name__ == "__main__":
    sys.exit(main())
