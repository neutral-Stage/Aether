# Aether — Advanced Roadmap (Phases 7–10)

> Status: planning. Phases 0–6 are shipped (see `ROADMAP.md`, `FLEET.md`). This
> document is the audited, research-backed plan for what comes next.
> Produced from a nine-agent audit: three codebase gap-audits → adversarial
> verification (false positives pruned) → three SOTA research streams → synthesis.

## Context

Aether can already operate a whole Mac by voice, control apps foreground and
background, browse, and spawn a fleet of coding agents. Phases 2–6 delivered the
fleet lifecycle, multi-app AX control, browser CDP attach, local voice + real
wake word, an MCP server, and multi-run. This roadmap targets the gap between
"impressive capability" and "a safe, installable product with a real moat."

### What is already solid (do not re-build)
- **Fleet lifecycle** — spawn, per-agent git-worktree isolation, watchdog
  (cost cap + timeout), steer, stop, streamed output (`aether/fleet/`).
- **Multi-run substrate** — concurrent runs, run registry, per-session cost caps,
  global STOP (`sidecar/server.py`).
- **Model router already has cross-provider failover** (`FailoverLLMClient` +
  `RouteTier` in `aether/core/router.py`). An audit claim of "no failover" was
  **refuted in verification** — tier-routing is an *extension* of existing
  machinery, not greenfield.
- **AX-tree-first perception is the right bet.** MacArena (2026) shows pure-vision
  agents collapse to ~0% on native multi-app macOS tasks; Aether exploits
  structured signal competitors leave behind. Keep it as the primary tier.

## The verified gaps (themes)

Each is confirmed against the code, not speculative.

**T1 — Cross-agent coordination (highest leverage / the moat).**
The fleet is a flat `dict` in `aether/fleet/manager.py` with a one-event-per-agent
sink in `sidecar/fleet_api.py`. There is **no dependency graph, no agent→agent
path, no result synthesis, and no conflict handling** when two agents touch the
same repo; nesting is capped at 1; `terminal`-type agents skip worktrees. You can
spawn N agents but cannot express "B depends on A, merge their diffs, don't let
them collide."
*Informed by:* shared-task-DAG orchestration (arXiv 2606.00953), Claude Code
Agent Teams (shared task list + file locking).

**T2 — Reliability & durability (de-risking).**
The sidecar holds **zero durable state** — a restart orphans every run and fleet
session. A run can wedge in `"running"` forever (state mutation outside a
try/finally in `sidecar/server.py`). Global STOP **cannot interrupt an in-flight
`subprocess.run`** (so `delegate_to_coder` ignores the panic button). Claude
Code's `--resume` is plumbed but unused.
*Informed by:* durable/event-sourced agent workflows (Temporal, LangGraph
checkpointers), Anthropic "resume from where the agent was."

**T3 — Safety & isolation (non-negotiable for always-on).**
The agent currently holds all three legs of the prompt-injection kill chain
(untrusted screen content + private-data access + ability to act) — a
**Rule-of-Two violation**. `aether/core/policy.py` is fleet-blind; `acceptEdits`
hides agent edits; the shell denylist is bypassable; sidecar auth is off by
default (`sidecar/auth.py`).
*Informed by:* Meta "Rule of Two", Google CaMeL (capability-scoped data flow),
OWASP "Lies-in-the-Loop" injection-of-approvals.

**T4 — Product & onboarding (adoption blocker).**
There is **no installable product**. The Swift app never starts the sidecar, has
no in-app API-key entry, no signed/notarized build, and no onboarding for the
three TCC permissions (Accessibility, Screen Recording, Mic). Today it needs a
Python sidecar + Swift build + hand-edited `.env`.

**T5 — Deeper computer-use & voice (differentiation).**
No semantic control tier above AX (Apple App Intents / MCP — macOS 26 Tahoe
exposes this); knowledge packs are read-only (`aether/knowledge/loader.py` — no
learning from successful runs); no critic/verifier agent; voice is the older
request/response chain, not duplex speech-to-speech.

---

## Phased plan (ordered by leverage, not difficulty)

### Phase 7 — Ship it (make it a real app) · effort M–L · ~3–4 wks
**Goal:** a signed DMG a non-developer can install and use by voice, no terminal.

Work items:
1. **Bundle + supervise the sidecar** from the Swift app via `SMAppService`
   (launch, health-check, restart on crash). New `macos/.../Core/SidecarSupervisor.swift`;
   ship the Python core as a bundled venv or PyInstaller binary.
2. **Sign + notarize a DMG** and make auto-update actually work
   (`macos/.../Core/UpdateChecker.swift` — currently a stub; wire Sparkle appcast
   or the GitHub-release fallback), and unify the version string across
   `VERSION` / Swift Info.plist / sidecar.
3. **In-app API keys → Keychain** + a real Settings scene
   (extend `AuditKeychain.swift` pattern; new `Settings/KeysView.swift`); sidecar
   reads keys from Keychain, not just `.env`.
4. **Auth on by default** — generate a per-install `AETHER_SIDECAR_TOKEN` on first
   run, inject into the Swift client and fleet mcp-config (`sidecar/auth.py`,
   `aether/fleet/manager.py::_mcp_config_path`).
5. **Discoverability** — an in-app "what can I say / which apps are supported"
   surface driven by the 33 knowledge packs + tool registry (`GET /tools/schemas`,
   pack list).

**Exit:** signed DMG installs clean → onboarding walks the 3 permissions + keys →
app starts its own sidecar → a spoken command completes end to end. No terminal.

### Phase 8 — The moat (cross-agent orchestration) · effort L · ~4–6 wks
**Goal:** turn the flat fleet into a real orchestrator with a dependency graph,
conflict avoidance, and result synthesis.

Work items:
1. **Task DAG as source of truth** — new `aether/fleet/graph.py`: nodes (tasks) +
   edges (depends-on), status propagation, ready-set scheduling. `SessionManager`
   schedules from the DAG instead of a flat dict.
2. **Cohesion / conflict gate** — before running two nodes in parallel, a
   `git merge-tree` pre-flight (in `aether/fleet/worktree.py`) proves their
   worktrees can't collide; colliding nodes serialize. Give `terminal` agents
   worktrees too.
3. **Integration / result-synthesis layer** — new `aether/fleet/synthesize.py`:
   merge worker diffs, route failures back as new DAG nodes, produce one combined
   result. Surface the DAG over `sidecar/fleet_api.py` (+ SSE) and render it in
   `macos/.../HUD/FleetView.swift`.
4. **Critic node + deterministic gate** — every code-producing node must pass a
   critic agent *and* a deterministic check (build/test/lint) before its diff is
   eligible to merge. Reuse `delegate_to_coder` + `run_shell`.
5. **Cost-tier routing with ceilings** — extend the existing `RouteTier` /
   `FailoverLLMClient` to pick agent/model tier by task difficulty under a
   per-DAG budget; stop the DAG when the ceiling is hit.
6. Adopt the A2A / MCP-Tasks **vocabulary** (task/artifact/status names) in the
   DAG schema — but not the wire protocol yet (see "defer").

**Exit:** a multi-part goal renders a DAG; agents run in parallel only where
`merge-tree` says it's safe; every code node passes critic+gate; you get one
synthesized result within a stated budget.

### Phase 9 — Survive (durability + safety) · effort L · ~4–5 wks
**Goal:** safe to leave always-on; survives restarts; STOP always works.

Work items:
1. **Durable, event-sourced state** — append-only run/fleet/DAG event log on disk;
   reconcile on sidecar startup (re-attach live agents via PID + `--resume`;
   mark truly-dead ones). New `sidecar/run_store.py`; touches `server.py`,
   `aether/fleet/manager.py`.
2. **Fix the reliability kernel** — move the wedged-run state mutation inside
   try/finally (`sidecar/server.py`); thread the STOP abort into
   `subprocess.run`/PTY so `delegate_to_coder` and fleet sessions die within ~1s
   (`aether/core/stop.py` closers → `aether/tools/delegation.py`); add
   same-provider exponential backoff for single-provider configs in the router.
3. **Rule-of-Two enforcer + quarantine boundary** — `aether/core/policy.py`
   becomes fleet-aware; tag untrusted screen/web content and block the
   untrusted-content + private-data + act trifecta; expose agent edits instead of
   silent `acceptEdits`.
4. **Injection-hardened approvals + hash-chained audit** — approval dialogs that
   show the exact validated operation (defeat "Lies-in-the-Loop"); extend the
   HMAC audit log to a hash chain (`aether/core/audit_log.py`).
5. **Spawn-depth + fleet-aggregate caps** — global ceilings so a runaway can't
   fork-bomb agents (`aether/fleet/manager.py`).

**Exit:** restart re-attaches running agents with no orphans; STOP halts a
delegated coder within a second; an injected "delete everything" surfaces the
exact operation for approval instead of executing.

### Phase 10 — Extend (differentiation) · effort L · ongoing
**Goal:** widen the capability lead once the base is safe and shippable.

- **App-Intents / MCP semantic control tier** above AX (macOS 26) — a new
  effector tier in `aether/effectors/` + knowledge-pack hooks; use for apps that
  expose intents, fall back to AX/vision.
- **Duplex voice** — speech-to-speech realtime path as the default when a key is
  present (graduate `beta.realtime_voice`), keeping local as the private fallback.
- **Pack write-back / learning** — successful runs distill into pack updates
  (`aether/knowledge/loader.py` + skill store), turning static YAML into learned
  expertise.
- **Hi-res vision + set-of-marks** for the vision tier when AX is thin.
- **Proactive `app_watcher` triggers** — watched-app events kick off actions, not
  just alerts.

### Phase 11 — Prove it (evals + live validation) · effort M · in progress
**Context:** Phases 2–10 shipped and are committed, but almost everything was
verified with mocks/fakes/fixtures — it has never run against real keys, real
coding CLIs, or a real signed build. This phase converts "built" into "proven"
and gets the objective safety number.

- **11.1 Scored safety harness** — a deterministic, in-repo red-team suite
  (`tests/benchmark/redteam.py` + `redteam_cases.yaml`) that measures the Phase-9
  Rule-of-Two defense: for a corpus of injected-destructive scenarios (untrusted
  on-screen content + a destructive tool call), what fraction is **surfaced for
  confirmation with the exact op** vs **auto-executed (a leak)**. Reports a single
  injection-defense rate + exact-op-shown rate + by-category + a defense-off
  contrast. `make redteam`. No keys — exercises `policy`/confirm logic directly.
- **11.2 Live e2e smoke** (`scripts/live_smoke.py` + runbook) — the keyless parts
  self-test here (sidecar boot, `/catalog`, run_store reconcile); the real-CLI/key
  steps (fleet spawn, a graph, STOP kill-latency) are scripted for the user to run
  on a real Mac, confirming the exit criteria hold against reality.
- **11.3 Close `run_request`** — wire the Swift SSE consumer so a proactive
  trigger's auto-run actually fires end-to-end (the review found the event has no
  consumer today).

**Exit:** `make redteam` reports a defense rate (target 100% surfaced / 0 leaked
on destructive cases, no false-surfacing on benign) with a defense-off contrast;
`live_smoke.py` passes its keyless checks and documents the real-CLI checklist;
proactive auto-run is functional end-to-end. Follow-on (external, needs infra):
OSWorld + AgentDojo/RedTeamCUA for headline capability + third-party safety numbers.

---

## What to NOT build (ponytail judgment)
- **Full A2A / MCP-Tasks wire protocol** — adopt the data model/vocabulary now,
  defer transport. MCP-Tasks was redesigned 2026-07-28; it's a moving target.
- **Autonomous swarm hand-off** — keep the orchestrator-worker spine; borrow only
  bounded peer messaging. Swarms are hard to reason about and harder to make safe.
- **Temporal / microVMs as a hard dependency** — a disk event-sourced log buys
  most of the durability without operational weight.
- **Self-hosting UI-TARS-2 / CUA / Gemini computer-use** — they invert to ~10% on
  macOS (MacArena); no edge over the Claude computer-use path already wired.
- **Replacing Kokoro / Porcupine** — churn without payoff; the voice win is
  *duplex*, not the vocoder.

## Evals (measure ourselves)
Priority order:
1. **OSWorld + OSWorld-Human** — headline capability + step-efficiency.
2. **RedTeamCUA + AgentDojo** — the most Aether-relevant safety benchmark. A/B
   each Phase-9 defense layer to *prove* the injection posture. The before/after
   delta is the number no competitor built on flat fan-out can show.
3. **SWE-bench Verified + Terminal-Bench** — for `delegate_to_coder` (report with
   Lucky-Pass / UTBoost caveats).
4. **τ²/τ³-bench + TheAgentCompany** — coordination + policy-gate reliability.

Reporting discipline: report **pass^k reliability** (HAL methodology) alongside
pass@1, always with **cost-per-task** in view. Wire an offline harness into
`tests/benchmark/` mirroring the existing mock/live split.

## Recommended sequencing
Do **Phase 7 first** — it unblocks everyone else actually using Aether and is the
cheapest path to real feedback. **Phase 8** is the differentiator (the moat) and
should follow once the app is installable. **Phase 9** is what makes it safe to
leave always-on — do not ship autonomous/always-on defaults before it lands.
Phase 10 is continuous.
