# Aether — Autonomous macOS AI Agent
## Engineering Specification & Product Requirements Document

| | |
|---|---|
| **Working codename** | Aether *(placeholder — rename freely)* |
| **Document type** | Engineering Spec / PRD |
| **Version** | 0.1 (Draft) |
| **Date** | June 16, 2026 |
| **Status** | For review |
| **Target platform** | macOS 15+ (Apple Silicon) |
| **Owner** | Shuvo |

---

## 1. Executive Summary

Aether is a always-available macOS assistant that a user drives entirely by **voice or text**, and that can **hear**, **see the screen in real time**, optionally **see through the camera**, and **act** on the machine the way a human would — moving the cursor, clicking, typing, running shell commands, writing and executing code, browsing the web, and operating any first- or third-party application. It replies in **natural voice** and on a lightweight on-screen overlay.

Architecturally, Aether is a **perception → reasoning → action** agent loop wrapped around a hybrid model stack: fast **local** models for low-latency speech and routine UI steps, and frontier **cloud** models (Claude Opus/Sonnet, GPT-class) for hard reasoning, planning, and vision. The product is delivered in two stages: a **Python prototype** to validate the loop quickly, then a **native Swift/SwiftUI app** for production-grade latency, OS integration, and permissions.

The defining engineering challenge is not any single capability — every piece below maps to a shipping API — but combining them into something **fast, real-time, reliable, and safe** with full-system reach.

### 1.1 Capability summary

| Capability | How it is delivered |
|---|---|
| Voice input | Streaming STT (local Parakeet/Whisper fast path; cloud Realtime for accuracy) |
| Text input | Global hotkey command bar |
| Hearing (ambient audio) | `AVAudioEngine` mic capture + VAD |
| Seeing the screen (real time) | `ScreenCaptureKit` continuous capture + Accessibility tree |
| Seeing (camera) | `AVFoundation` camera capture (opt-in) |
| Acting on any app | Accessibility API (`AXUIElement`), `CGEvent`, AppleScript/Apple Events |
| Coding | Sandboxed code-exec tool (Python/Node/shell) + editor automation |
| Browsing | Browser automation (CDP / Playwright / native automation) |
| Voice output | Streaming TTS (local Kokoro/Piper fast path; cloud for quality) |
| Reasoning / planning | Hybrid model router: local small model + cloud frontier model |
| Memory | Session context + long-term vector store + per-app knowledge |
| Tool use | Internal effectors + MCP client for external tools/services |
| Professional app mastery | Per-app **Knowledge Packs** + tiered control (scripting API → AX+key-commands → vision) — Office, Resolve, Logic Pro, etc. |
| Specialist delegation | Orchestrates agentic coders (Claude Code, Codex, OpenCode, Kilo, Cursor, Antigravity) as supervised sub-agents |

---

## 2. Goals & Non-Goals

### 2.1 Goals

- **G1 — Universal control.** Operate any application on macOS (built-in, third-party, web) using the same input/output channels a human uses, plus privileged automation APIs where available.
- **G2 — Real-time feel.** Conversational voice latency target **< 800 ms** to first audio; perceptible action begins **< 1.5 s** after a command.
- **G3 — Multimodal awareness.** Continuous, low-cost awareness of on-screen state; on-demand high-resolution vision and camera.
- **G4 — Hands-free or hands-on.** Fully usable by voice alone, fully usable by text alone, and seamless when mixing both.
- **G5 — Powerful + extensible.** First-class code execution, terminal, browser, and file access; extensible via MCP and plugins.
- **G6 — Safe by construction.** Explicit permission gating, confirmation for destructive/irreversible actions, full audit log, instant "stop."
- **G7 — Private where it matters.** Local fast paths keep routine perception/voice on-device; cloud calls are explicit and minimizable.

### 2.2 Non-Goals (v1)

- **NG1** — Cross-platform (Windows/Linux) clients. Architecture stays portable, but v1 ships macOS only.
- **NG2** — Mac App Store distribution. The required entitlements (control of other apps, screen recording) are incompatible with App Store sandboxing; v1 ships as a Developer ID-signed, notarized app.
- **NG3** — Fully autonomous long-horizon operation with zero supervision. v1 is supervised-autonomy: it acts continuously but within guardrails and stop controls.
- **NG4** — Mobile companion app. Out of scope for v1 (candidate for v2).

### 2.3 Success Metrics

| Metric | Target (v1) |
|---|---|
| Voice round-trip (speech-in → first audio-out) | p50 < 800 ms, p95 < 1.5 s |
| Simple UI task success (e.g. "reply to this email") | ≥ 85% unattended |
| Multi-step task success (5–10 steps) | ≥ 60% unattended |
| Mean steps to recover from a misclick | ≤ 1 (self-correcting) |
| Crash-free sessions | ≥ 99% |
| User "stop" honored | < 200 ms, 100% |

---

## 3. Product Requirements

Requirements are tagged **FR** (functional) and **NFR** (non-functional). Priority: **P0** = v1 must-have, **P1** = v1 should-have, **P2** = later.

### 3.1 Functional Requirements

**Input & Activation**

- **FR-1 (P0)** — Activate by global hotkey (text command bar) and by voice wake word ("Hey Aether") or push-to-talk.
- **FR-2 (P0)** — Accept free-form natural language by voice or text, interchangeably, within one session.
- **FR-3 (P1)** — Support barge-in: the user can interrupt Aether's speech and it stops and listens immediately.

**Perception**

- **FR-4 (P0)** — Continuously capture the active display and foreground window via ScreenCaptureKit.
- **FR-5 (P0)** — Extract the Accessibility tree of the foreground app (roles, titles, values, frames) as the primary, cheap "what's on screen" signal.
- **FR-6 (P0)** — On demand, send a high-resolution screenshot (optionally with element-grounding overlays) to a vision model.
- **FR-7 (P1)** — Capture microphone audio with voice-activity detection; optional ambient "always listening" mode (off by default).
- **FR-8 (P2)** — Capture camera frames on explicit request ("look at this").

**Reasoning & Planning**

- **FR-9 (P0)** — Maintain a planner that decomposes a goal into steps and an executor that performs each step and observes the result.
- **FR-10 (P0)** — Route each model call to the cheapest sufficient model (local small model → cloud frontier model) based on task difficulty.
- **FR-11 (P0)** — Ground actions in real UI coordinates/elements, preferring Accessibility targets over raw pixel coordinates.
- **FR-12 (P1)** — Self-correct: detect when an action did not produce the expected state and retry/replan.

**Action / Effectors**

- **FR-13 (P0)** — Move cursor, click (single/double/right), drag, scroll, and type via `CGEvent`.
- **FR-14 (P0)** — Read and set UI element values and invoke actions via the Accessibility API.
- **FR-15 (P0)** — Run shell commands and scripts in a controlled environment; capture stdout/stderr.
- **FR-16 (P0)** — Read/write files within user-approved scopes.
- **FR-17 (P1)** — Drive AppleScript/Apple Events for apps that expose scripting dictionaries (Mail, Finder, Notes, etc.).
- **FR-18 (P1)** — Browse the web: open pages, read DOM/text, click, fill forms, extract data.
- **FR-19 (P1)** — Write, run, and iterate on code (Python/Node/shell) in a sandboxed workspace.
- **FR-20 (P2)** — Call external tools/services via an MCP client (calendar, email, issue trackers, etc.).

**Output**

- **FR-21 (P0)** — Stream spoken responses with natural TTS; show a synchronized text transcript on an overlay HUD.
- **FR-22 (P0)** — Display current intent/plan and the action in progress on the HUD ("Clicking 'Send'…").
- **FR-23 (P1)** — Surface confirmations for destructive actions inline (voice + HUD), with voice or click approval.

**Memory**

- **FR-24 (P1)** — Persist session history and a long-term memory of user preferences, frequent tasks, and app-specific knowledge.
- **FR-25 (P2)** — Learn reusable "skills"/macros from repeated successful task traces.

**Control & Safety**

- **FR-26 (P0)** — Global "STOP" (hotkey + voice) halts all action within 200 ms.
- **FR-27 (P0)** — Per-capability permission toggles (screen, mic, camera, files, shell, network, app control).
- **FR-28 (P0)** — Confirmation gate for irreversible/high-impact actions (delete, send, purchase, `rm`, git push, etc.).
- **FR-29 (P1)** — Append-only audit log of every perceived state summary and every action taken.

### 3.2 Non-Functional Requirements

- **NFR-1 Latency.** Voice first-audio p50 < 800 ms. UI action cadence ≥ 1 action/second on the fast path.
- **NFR-2 Throughput of perception.** Accessibility snapshot < 50 ms; screen frame grab < 30 ms; vision call only when needed.
- **NFR-3 Reliability.** Graceful degradation if cloud is unreachable (local-only mode with reduced capability).
- **NFR-4 Privacy.** No audio/screen data leaves the device except on explicit cloud calls; redaction layer for secrets fields.
- **NFR-5 Security.** Least-privilege; secrets in Keychain; no plaintext credentials in logs; signed + notarized binary.
- **NFR-6 Cost.** Per-task cloud cost target < $0.05 median by routing routine steps to local models.
- **NFR-7 Resource use.** Idle CPU < 3%; idle memory < 600 MB; no audible fan during ambient listening.
- **NFR-8 Observability.** Structured traces for every loop iteration (latency, model used, tokens, action, outcome).

---

## 4. Interaction Model

### 4.1 Modes

- **Command-bar (text):** A global hotkey (e.g. ⌥Space) opens a Spotlight-style bar. Type a request; results stream into the HUD.
- **Push-to-talk:** Hold a hotkey to speak; release to send. Lowest-friction, most private.
- **Wake word:** "Hey Aether" opens a listening session. Off by default; user-enabled.
- **Ambient / continuous:** Always listening with on-device VAD + wake-word gating. Opt-in, with a always-visible indicator.

### 4.2 The HUD (overlay)

A small, always-on-top, click-through-when-idle panel that shows: live transcript, current plan/step, the action in progress, a prominent **STOP**, and a **mic/recording indicator** for trust. It never fully obscures the screen and can be summoned/dismissed by hotkey.

### 4.3 Conversational behavior

- **Barge-in:** User speech during TTS instantly ducks/stops output (half-duplex → full-duplex with echo cancellation).
- **Grounded confirmations:** Before irreversible actions, Aether states what it's about to do and waits for "yes"/click.
- **Narration:** Optional running commentary ("Opening Safari, going to the orders page…") for trust; can be silenced.
- **Clarification:** When confidence is low or the target is ambiguous, it asks one concise question rather than guessing.

---

## 5. System Architecture

### 5.1 Design principles

1. **Accessibility-first, vision-second.** The Accessibility tree is cheap, structured, and precise; use it as the default percept and fall back to screenshot+vision only when the tree is insufficient (Canvas/Electron/games). This is the single biggest latency and reliability lever.
2. **Dual-loop (fast/slow).** A **fast loop** handles voice turn-taking and reflexive UI steps with local models in tens of milliseconds; a **slow loop** handles planning and hard perception with cloud frontier models. They run concurrently and communicate through a shared world-model/blackboard.
3. **Everything is a tool.** Perception and action are exposed to the reasoning core as a uniform tool interface, so internal effectors and external MCP tools are interchangeable.
4. **Supervised autonomy.** The loop acts continuously but every high-impact action passes a policy gate; the user can stop or take over at any instant.
5. **Stateless models, stateful runtime.** Models are called statelessly; durable state (world model, memory, audit) lives in the runtime so we can swap/upgrade models freely.

### 5.2 High-level diagram

```mermaid
flowchart TB
    subgraph User
      V[Voice / Mic]
      T[Text / Hotkey]
      SPK[Speakers]
      HUD[On-screen HUD]
    end

    subgraph Perception
      STT[Streaming STT]
      SC[ScreenCaptureKit frames]
      AX[Accessibility tree reader]
      CAM[Camera capture]
      OCR[On-device OCR / element grounding]
    end

    subgraph Core["Reasoning Core (Orchestrator)"]
      WM[(World Model / Blackboard)]
      RT[Model Router]
      PLAN[Planner]
      EXEC[Executor]
      MEM[(Memory: session + vector store)]
      POL[Safety / Policy Gate]
    end

    subgraph Models
      LOCAL[Local small LLM + VLM]
      CLOUD[Cloud frontier LLM/VLM]
    end

    subgraph Effectors
      CG[CGEvent mouse/keyboard]
      AXA[Accessibility actions]
      AS[AppleScript / Apple Events]
      SH[Shell / Code exec]
      BR[Browser automation]
      MCP[MCP client → external tools]
    end

    subgraph Output
      TTS[Streaming TTS]
    end

    V --> STT --> WM
    T --> WM
    SC --> OCR --> WM
    AX --> WM
    CAM --> WM
    WM <--> RT
    RT <--> LOCAL
    RT <--> CLOUD
    WM --> PLAN --> EXEC
    EXEC --> POL
    POL --> CG & AXA & AS & SH & BR & MCP
    CG & AXA & AS & SH & BR & MCP --> WM
    EXEC --> MEM
    PLAN --> TTS --> SPK
    EXEC --> HUD
    POL -. confirm .-> HUD
```

### 5.3 The agent loop

```
loop (each turn / each step):
  1. PERCEIVE  → refresh world model: STT text, AX tree delta, (optional) screenshot, system state
  2. ORIENT    → router picks model tier; planner updates/affirms the plan given the goal + world model
  3. DECIDE    → choose the next single action (tool call) with grounded target (AX element or coords)
  4. GATE      → policy check: allowed? destructive? needs confirmation? → ask user if required
  5. ACT       → effector performs the action
  6. OBSERVE   → capture resulting state; verify expected change; on mismatch → replan (back to 2)
  7. SPEAK     → stream narration/answer via TTS; update HUD
  until goal satisfied or user stops
```

### 5.4 Process & concurrency model

- **Audio engine** runs on a real-time thread (mic in, VAD, echo cancellation, TTS out) — never blocked by model calls.
- **Perception workers** (ScreenCaptureKit stream, AX reader) run on dedicated queues feeding the world model.
- **Reasoning core** runs async; cloud calls are streamed and cancellable.
- **Effector executor** is serialized (one action at a time) and pre-emptible by STOP.
- **IPC:** In the Swift app, a privileged helper (or the main app with the right entitlements) performs control; the Python prototype runs in one process for simplicity.

---

## 6. Component Specifications

### 6.1 Voice I/O subsystem

**Responsibilities:** capture mic audio, detect speech, transcribe in real time, synthesize speech out, manage turn-taking and barge-in.

**Pipeline (fast path, local):**
`AVAudioEngine` mic tap → ring buffer → VAD (Silero/WebRTC) → streaming STT → partial transcripts to world model. TTS: token-stream from the model → streaming neural TTS → `AVAudioEngine` player node. Acoustic echo cancellation so the mic doesn't hear the TTS.

**Pipeline (quality path, cloud):** A native **speech-to-speech Realtime** session (OpenAI Realtime / Gemini Live) for the most natural turn-taking when network allows; used for open conversation, while the local path is used for command dictation and offline.

| Concern | Decision |
|---|---|
| Wake word | On-device keyword spotter (e.g. openWakeWord / porcupine-style); never streams audio pre-wake |
| VAD | Local, < 30 ms frames; endpointing tuned for snappy turn-ends |
| STT (fast/local) | NVIDIA Parakeet / `whisper.cpp` (Metal) streaming; ~real-time on Apple Silicon |
| STT (accuracy/cloud) | OpenAI `gpt-realtime` streaming transcription / Deepgram |
| TTS (fast/local) | Kokoro / Piper — low-latency, on-device |
| TTS (quality/cloud) | ElevenLabs / OpenAI / Gemini TTS for natural prosody |
| Barge-in | Mic stays open during TTS; user speech > threshold ducks then cancels TTS |
| Latency budget | endpoint 100–200 ms + STT 50–150 ms + reason 200–400 ms + TTS first chunk 100–250 ms |

### 6.2 Perception subsystem

**Responsibilities:** maintain a continuously fresh, structured model of "what is on screen and what the system state is," cheaply.

- **Accessibility reader (primary).** Walks `AXUIElementCopyAttributeValue` for the focused app: role, title, value, enabled, focused, and `kAXFrame` (screen coordinates) for every actionable element. Produces a compact, diffable JSON tree. This is the cheapest and most reliable percept and yields **exact click targets** without pixel guessing. Cache + observe `AXObserver` notifications to update only on change.
- **ScreenCaptureKit (secondary).** A continuous capture stream of the active display/window. Frames are kept in memory; we only encode/downscale and ship to a vision model when needed (FR-6). Supports per-window capture to avoid leaking other content.
- **On-device OCR + grounding.** Apple Vision framework (`VNRecognizeTextRequest`) for text in apps that don't expose AX (Electron, Java, games, remote desktops). Optional small grounding VLM (e.g. UI-TARS-class) to map "the blue Send button" → bounding box when AX fails.
- **Camera (tertiary, opt-in).** `AVCaptureSession` frames on explicit request only.
- **System state.** Frontmost app, window list, clipboard, selected text, running processes, network/battery — cheap signals that improve grounding and safety.

**World model:** a single in-memory structure holding the latest AX tree, last screenshot ref, transcript, system state, the active goal, the plan, and a short rolling history. Everything the reasoning core sees flows through here.

### 6.3 Reasoning core (Orchestrator)

**Responsibilities:** turn goals + world model into grounded actions, using the right model at the right time.

- **Model Router.** Classifies each step: trivial/reflexive (local small LLM), perception-heavy (VLM — local grounding or cloud vision), or hard-reasoning/planning (cloud frontier). Routing inputs: task novelty, AX-sufficiency, prior failure count, user "careful mode." Goal: keep ~70–80% of steps on the local/cheap path.
- **Planner.** Produces and maintains a short plan (a few steps ahead, not a brittle full script). Re-plans on observation mismatch. Uses the frontier model for initial decomposition of novel goals; caches plans for known tasks.
- **Executor.** Picks the single next tool call with a concrete grounded target, emits it to the policy gate, then observes. Implements verify-after-act (did the expected AX change happen?).
- **Context manager.** Builds each model prompt from world model + memory within a token budget; compresses history; injects the tool schema and the current screen summary (AX text, not raw pixels, when possible).
- **Prompt/agent contract.** A strict tool-calling schema (JSON) defines every action; the model never "types free text that we parse" — it emits structured tool calls only.

### 6.4 Effectors (Action layer)

All effectors share one interface (`Tool.invoke(args) -> Observation`) and are individually permissioned.

| Effector | Mechanism | Notes |
|---|---|---|
| Mouse/keyboard | `CGEventCreateMouseEvent`, `CGEventCreateKeyboardEvent`, `CGEventPost` | OS-level synthetic input; works everywhere; coordinates from AX frames |
| UI actions | `AXUIElementPerformAction` (e.g. `AXPress`), `AXUIElementSetAttributeValue` | Click/set without moving the mouse; more reliable than pixel clicks |
| App scripting | AppleScript / Apple Events (`osascript`, ScriptingBridge) | Best for Mail, Finder, Notes, Music, Keynote, etc. |
| Shell / code | Subprocess in a controlled workspace; PTY for interactive tools | stdout/stderr captured; resource + path limits |
| Code authoring | Editor automation (VS Code CLI / AX) + file writes + run/iterate | "Write and run code" loop |
| Browser | Chrome DevTools Protocol / Playwright, or native AX of the browser | DOM-level read/click/fill is faster & more reliable than pixels |
| External services | MCP client | Calendar, email, trackers, custom org tools |

**Grounding rule:** prefer AX action → then scripted action → then synthetic click on AX frame → then vision-grounded pixel click, in that order of preference.

### 6.5 Tool layer & MCP

A registry exposes every effector and external integration as a typed tool with a JSON schema, permission tag, and "destructive?" flag. External capabilities are added via an **MCP client**, so new services (Slack, GitHub, Notion, calendar, etc.) drop in without core changes. The same registry powers the model's tool-calling menu and the policy gate.

### 6.6 Memory

- **Session memory:** rolling transcript, plan, recent actions/observations (in world model).
- **Long-term memory:** a local vector store (e.g. SQLite + `sqlite-vec`, or LanceDB) holding user preferences, frequent task traces, app-specific quirks ("in this app, Send is ⌘↵"), and corrections. Retrieved by similarity at planning time.
- **Skill/macro memory (P2):** successful multi-step traces distilled into reusable parameterized skills to skip re-planning and cut latency/cost.

### 6.7 Safety & policy gate

- **Capability permissions:** per-feature toggles (screen, mic, camera, files, shell, network, app control), enforced in the runtime regardless of what the model requests.
- **Action classification:** every tool call is tagged read-only / reversible / **irreversible-or-high-impact** (delete, send, pay, `rm -rf`, `git push`, mass file ops, anything touching money or external comms). High-impact → mandatory confirmation.
- **Scopes:** file and shell access constrained to approved roots; network egress allow-listed in careful mode.
- **Secret redaction:** password/secure-text AX fields and detected credentials are never sent to cloud or written to logs.
- **STOP:** a hardware-level hotkey monitor and a voice "stop" both flip a kill switch the executor checks before every action and that cancels in-flight effectors.
- **Audit log:** append-only, local, signed; records every percept summary, decision, action, and confirmation for full traceability.

---

### 6.8 App Integration Tiers (the control ladder)

Apps expose wildly different control surfaces, so Aether picks the **highest-fidelity method each app supports for the task at hand**, and combines tiers when useful.

| Tier | Method | Fidelity / speed | Example apps |
|---|---|---|---|
| **1 — Scripting API** | Official API / scripting dictionary | Highest: deterministic, no UI guessing | DaVinci Resolve (Python/Lua), MS Office (AppleScript/VBA), Finder/Mail/Notes/Keynote (AppleScript), browsers (CDP), terminal/CLIs |
| **2 — Accessibility + Key Commands** | AX tree read/act + known shortcuts | High: structured, fast | VS Code, Cursor, most native & Electron apps |
| **3 — Vision + Key Commands + domain knowledge** | ScreenCaptureKit + VLM grounding + key-command playbook | Medium: needed for custom canvases | Logic Pro, Resolve color/Fusion, Photoshop, games |
| **4 — Synthetic pixel input** | CGEvent clicks on grounded coordinates | Fallback of last resort | anything else |

There is also a **Tier 0 — Delegation to specialist agents**: for coding, Aether does not reinvent a coding agent — it **orchestrates** best-in-class agentic coders (Claude Code, Codex CLI, OpenCode, Kilo, Gemini CLI, or GUI IDEs Cursor/Antigravity) as sub-agents and supervises them (see 6.10).

> Rule: always climb to the highest tier an app supports, and blend tiers — e.g. Resolve uses the scripting API for timeline edits *and* vision for color-grading judgment.

### 6.9 App Knowledge Packs — how Aether becomes an expert

"A lot of expertise" is an explicit subsystem, not an emergent hope. Each supported app has a **Knowledge Pack**: a retrievable module that is loaded into context when that app is frontmost (and indexed in the vector store, §6.6).

A pack contains:

- **Integration manifest** — which tier(s) apply, scripting entry points, key APIs.
- **Key-command cheat sheet** — the app's important keyboard shortcuts (the fastest, most reliable control channel in pro apps).
- **Workflow recipes** — step-by-step procedures for common tasks ("color-grade a clip," "bounce a mix," "build a multicam edit," "create a pivot table," "set up a CI workflow").
- **Domain best practices / expert persona** — e.g. a colorist's node-graph conventions for Resolve, gain-staging/EQ/compression/mastering rules for Logic, financial-modeling conventions for Excel.
- **Scripting snippets** — parameterized, ready-to-run API calls (Resolve Python, Office AppleScript) for deterministic operations.
- **Gotchas** — known failure modes and how to avoid them.

How packs are sourced and grow:

- **Curated/authored** for headline apps — RAG-indexed official manuals + hand-written recipes.
- **Learned** — successful task traces distilled into new recipes/macros (skill memory, §6.6), so it gets better with use.
- **Shareable** — packs are versioned files; a community ecosystem can publish them (like open-source skills), so expertise compounds across users.

At plan time the router retrieves the relevant pack slices for the current app + task, so the frontier model reasons with **expert context** instead of from scratch. This is what turns "can click buttons" into "knows how a pro uses this app."

### 6.10 Professional application playbooks

#### Native & system apps
Finder, Mail, Notes, Calendar, Reminders, Safari, Messages, System Settings, Preview, Photos, Music. Strategy: **AppleScript / Apple Events** where a scripting dictionary exists (Finder, Mail, Notes, Music, Keynote, etc.), **Accessibility** otherwise. Fast, reliable, covers the bulk of everyday tasks.

#### Office & documents
- **Microsoft 365 (Word/Excel/PowerPoint/Outlook):** AppleScript + VBA on macOS, or an Office automation/MCP path — deterministic doc/cell/slide manipulation (Tier 1). Excel modeling, Word formatting, and deck-building are scriptable end to end.
- **Apple iWork (Pages/Numbers/Keynote):** AppleScript dictionaries.
- **Google Workspace:** browser automation (CDP) or Google APIs via MCP.

#### Terminal & shell
Full **PTY** control: run commands, drive interactive TUIs, manage long-running processes, stream output. Also the launchpad for delegated coding agents below.

#### IDEs & AI coding tools
Two complementary modes:

1. **Drive the editor** — VS Code / Cursor / Antigravity via Accessibility + their command palettes + CLIs (`code`, etc.) to open files, run tasks, and read the workspace.
2. **Delegate to agentic coders (Tier 0)** — for real coding work, Aether writes a precise spec and hands it to a specialist CLI agent in a scoped workspace, then supervises (watch output, read diffs, run tests, report by voice). Indicative landscape (mid-2026):

| Tool | Type | Control surface | Notes |
|---|---|---|---|
| Codex CLI | CLI agent | terminal | GPT-5.5; ~83.4% Terminal-Bench 2.1 (top) |
| Claude Code | CLI agent | terminal / SDK | Opus 4.8; ~78.9% Terminal-Bench 2.1 |
| OpenCode | CLI agent (OSS, MIT) | terminal | 75+ providers, BYOK, ~172k★ |
| Kilo (CLI 1.0) | CLI agent (OSS, MIT) | terminal + IDE | 500+ models, local-model capable |
| Gemini CLI | CLI agent (OSS) | terminal | Gemini 3.x |
| Cursor | AI IDE (GUI) | AX + CLI | drive via editor automation |
| Antigravity | Agent-first IDE (GUI) | AX + agents | Gemini-powered, parallel agents |

Pattern: Aether is the **supervising orchestrator** that picks the best/cheapest coder for the job, integrates the result, and keeps the human in the loop. BYOK/local-model agents (OpenCode, Kilo) keep cost and privacy in check.

#### Video editing — DaVinci Resolve
- **Tier 1 (scripting):** the official **Python (Studio) / Lua (free + Studio)** API automates project, media-pool, timeline, import/render, metadata, and Fusion operations — deterministic and fast.
- **Tier 3 (vision + key commands):** color grading, Fusion node artistry, and the **audio mixer/EQ/dynamics/fades/bus routing — which the API explicitly cannot script** — are driven via UI automation + key commands + colorist/editor domain knowledge from the Knowledge Pack.
- Premiere Pro (ExtendScript/UXP) and Final Cut Pro (limited AppleScript + FCPXML) follow the same tier-blend principle.

#### Audio production — Logic Pro
- **No public app-automation API.** Strategy is **Tier 3**: Accessibility where exposed, an extensive **Key Commands** playbook, ScreenCaptureKit/vision for the mixer and plugin UIs, and deep audio domain knowledge (gain staging, EQ, compression, arrangement, mastering) in the Knowledge Pack.
- **Scripter** (Logic's built-in JavaScript MIDI plugin) handles programmatic **MIDI generation/processing**.
- System-level AppleScript / UI scripting via System Events covers menu and transport actions.
- The same approach generalizes to Pro Tools, Ableton Live (which adds a Python/Max control surface), and FL Studio.

> Design implication: pro creative apps make **vision + key-command expertise indispensable** — which is exactly why the Knowledge Pack system (6.9) and the vision fallback (6.2) are core to the architecture, not optional extras.

---

## 7. Technology Stack

### 7.1 Stack by layer

| Layer | Prototype (Python) | Production (Native) |
|---|---|---|
| App shell / UI | Menu-bar app (rumps) + simple overlay | **Swift + SwiftUI**, `NSPanel` HUD, menu-bar item |
| Audio capture/playback | `sounddevice` / PyAudio | **AVFoundation / AVAudioEngine** |
| VAD | Silero VAD (PyTorch) / webrtcvad | Silero (Core ML) / native VAD |
| STT (local) | `whisper.cpp` / Parakeet via MLX | **MLX / Core ML** Parakeet or whisper.cpp |
| STT/voice (cloud) | OpenAI Realtime / Gemini Live SDK | Same via URLSession/WebSocket |
| TTS (local) | Kokoro / Piper | Kokoro / Piper (Core ML) or `AVSpeechSynthesizer` |
| TTS (cloud) | ElevenLabs / OpenAI / Gemini | Same |
| Screen capture | `mss` / pyobjc ScreenCaptureKit | **ScreenCaptureKit** |
| Accessibility | `pyobjc` (ApplicationServices) | **ApplicationServices / AXUIElement** (Swift) |
| OCR / vision (local) | Apple Vision via pyobjc / RapidOCR | **Vision.framework**, Core ML VLM |
| Input synthesis | `pyobjc` Quartz `CGEvent` | **CoreGraphics CGEvent** |
| App scripting | `osascript` subprocess | ScriptingBridge / NSAppleScript |
| Browser | Playwright | Playwright (Node helper) or CDP |
| Reasoning models | API SDKs | API SDKs + on-device MLX |
| Local LLM/VLM | **MLX** / Ollama (Llama/Qwen/UI-TARS-class) | **MLX** (Apple Silicon native) |
| Memory store | SQLite + sqlite-vec / LanceDB | Same |
| Tooling protocol | MCP Python SDK | MCP Swift/Node client |
| Orchestration | Custom async loop (asyncio) | Swift Concurrency (actors) + helper procs |

### 7.2 Model choices (mid-2026)

The router blends tiers. Indicative current options:

- **Frontier reasoning + vision (cloud, "slow loop"):** Claude **Opus 4.8** (top OSWorld-Verified ~83% as of May 2026) for hardest planning/vision; **Claude Sonnet 4.6** as the default agentic workhorse (strong computer-use at lower cost/latency); GPT‑5.5-class as an alternative/fallback. Computer-use is now a first-class, generally-available capability on these models.
- **Fast reasoning (cloud or local, "fast loop"):** Claude **Haiku**-class or a local **MLX** small model (Qwen/Llama-class) for routine step selection, classification, and narration.
- **GUI grounding VLM:** a **UI-TARS**-class open model (ByteDance; 7B local / 72B server) or Agent‑S-style framework to convert visual targets → coordinates when AX is insufficient. Runnable locally on Apple Silicon with a GPU/unified memory.
- **Realtime voice:** **OpenAI Realtime (gpt-realtime)** or **Google Gemini Live** for end-to-end speech-to-speech (first audio ~300–800 ms); local Parakeet/whisper.cpp + Kokoro/Piper for the offline/private fast path.

> Model IDs move fast — treat these as slots in the router, not hardcoded dependencies. The router config maps roles (`planner`, `fast`, `grounding`, `voice`) → concrete model IDs so upgrades are a config change.

### 7.3 Why hybrid Python → Swift

- **Python first** to validate the agent loop, prompts, routing, and tool contracts in days, with the richest ML ecosystem.
- **Swift for production** because OS-level control, low-latency audio, ScreenCaptureKit, Accessibility, and TCC permissions are first-class and far faster/cleaner natively; the menu-bar/HUD UX is native; and code-signing/notarization/entitlements are required for distribution anyway.
- The **tool contract and world-model schema are language-agnostic** (JSON), so logic ported from Python maps cleanly onto Swift actors. Heavy ML that's painful in Swift (local VLM) can stay in a sidecar process (MLX/Python) behind the same tool interface.

---

## 8. End-to-End Data Flow & Latency Budget

### 8.1 Trace: "Aether, reply to this email saying I'll join at 3pm"

1. **Wake/endpoint** — wake word + VAD detect a complete utterance. *(~150 ms)*
2. **STT** — streaming transcript finalized. *(~120 ms, overlaps speech)*
3. **Perceive** — AX tree of Mail captured; foreground = Mail, an email is selected; no screenshot needed. *(~40 ms)*
4. **Route + plan** — router sends goal + AX summary to Sonnet (fast tier sufficient): plan = [open reply, type body, confirm-before-send]. *(~300 ms streamed)*
5. **Act (step 1)** — `AXPress` the Reply button (found in AX tree → no pixel click). *(~30 ms + app redraw)*
6. **Observe** — AX shows compose window open with a focused text field. *(~40 ms)*
7. **Act (step 2)** — type body via `CGEvent` / set AX value. *(~80 ms)*
8. **Gate** — "Send" is high-impact → confirmation. Aether speaks "Ready to send your reply — should I send it?" *(TTS first audio ~200 ms)*
9. **Confirm** — user: "yes." STT → gate cleared. *(~250 ms)*
10. **Act (step 3)** — `AXPress` Send. **Done.** Aether: "Sent." 

### 8.2 Latency budget (fast path, per conversational turn)

| Stage | Target |
|---|---|
| Endpointing (VAD) | 100–200 ms |
| STT (streaming, local) | 50–150 ms |
| Perception (AX snapshot) | 20–50 ms |
| Routing + reasoning (fast tier) | 200–450 ms |
| First TTS audio chunk | 100–250 ms |
| **Speech-in → first audio-out (p50)** | **< 800 ms** |
| Per UI action (AX/CGEvent) | 20–120 ms + app redraw |
| Vision call (only when AX insufficient) | +400–900 ms (slow path) |

### 8.3 Latency engineering techniques

- **AX over vision** wherever possible (10–20× cheaper than a screenshot+VLM round trip).
- **Stream everything**: STT partials, model tokens, TTS chunks — never wait for completes.
- **Speculative/parallel**: start TTS of the first sentence while the model is still generating; pre-warm the next AX snapshot during reasoning.
- **Local fast loop** keeps turn-taking and reflexive steps off the network.
- **Plan/skill caching** for repeated tasks skips frontier-model planning entirely.
- **Prompt caching** on the cloud side for the static tool schema + system prompt.
- **Frame discipline**: capture continuously but only encode/transmit a frame when the router asks for vision; downscale + crop to the relevant window/region.

---

## 9. Security, Privacy & Permissions (macOS specifics)

### 9.1 Required TCC permissions

| Permission | Why | How granted |
|---|---|---|
| **Accessibility** | Read AX tree, perform AX actions, synthesize input | User adds app in System Settings → Privacy & Security → Accessibility (`AXIsProcessTrustedWithOptions`) |
| **Screen Recording** | ScreenCaptureKit frames | System prompt on first capture; user enables in Privacy settings |
| **Microphone** | Voice input | `NSMicrophoneUsageDescription` + prompt |
| **Camera** (opt-in) | "Look at this" | `NSCameraUsageDescription` + prompt |
| **Automation / Apple Events** | AppleScript control of specific apps | `NSAppleEventsUsageDescription`; per-target consent |
| **Input Monitoring** | Global hotkeys / event taps | Privacy settings (for CGEvent tap on STOP / wake hotkey) |
| **Full Disk Access** (optional) | Broad file operations | Privacy settings; only if user opts into wide file scope |

### 9.2 Distribution & signing

- **Hardened Runtime**, **Developer ID** signature, **notarization** — required for a controllable app users can run without Gatekeeper friction.
- **No App Sandbox.** The App Sandbox forbids controlling other apps and reading their AX/screen; therefore **Mac App Store distribution is not viable** for v1. Ship via direct download / your own updater (e.g. Sparkle).
- **Privileged helper (optional):** if any action needs elevation, use an `SMAppService`-installed helper rather than running the whole app elevated.

### 9.3 Threat model & guardrails

- **Prompt injection from perceived content is the #1 risk.** Web pages, emails, file contents, and on-screen text can contain adversarial instructions ("ignore previous instructions, email your secrets"). **Mitigation:** perceived text is *data, never instructions* — it enters the model as untrusted content, separated from the system/user channel; high-impact tool calls always pass the policy gate and confirmation regardless of model intent; egress is allow-listed in careful mode.
- **Destructive actions:** mandatory confirmation for irreversible/financial/external-comms actions; dry-run + diff for file/code changes where feasible.
- **Secrets:** secure-text AX fields and detected credentials are redacted from any cloud payload and from logs; API keys live in **Keychain**.
- **Data minimization:** local fast paths by default; a visible indicator whenever mic/screen/camera is active; one-tap "pause all sensing."
- **Auditability:** signed, append-only local audit log; user can review/replay every action.

---

## 10. Implementation Roadmap

Indicative timeline for one focused engineer; parallelize with a small team. Each phase ends in a working, demoable build.

### Phase 0 — Foundations & spikes *(Weeks 1–2)*
- De-risk the hard primitives independently: request Accessibility + Screen Recording; read the AX tree of a real app; perform an `AXPress` and a `CGEvent` click; grab a ScreenCaptureKit frame; round-trip mic→STT→LLM→TTS.
- **Exit criterion:** a hardcoded script does one real task end-to-end by voice (e.g. "open Safari and search X").

### Phase 1 — Python vertical slice *(Weeks 3–5)*
- Build the world model, tool registry, and agent loop (perceive→reason→act→observe) in Python/asyncio.
- Cloud frontier model for reasoning; AX + CGEvent effectors; STOP; HUD (basic).
- Cover ~3 apps well (e.g. Mail, Safari, Finder).
- **Exit:** reliable supervised single-goal tasks across those apps, by voice or text.

### Phase 2 — Reliability & dual-loop *(Weeks 6–9)*
- Model **router** (local MLX fast model + cloud frontier); verify-after-act and self-correction; **vision fallback** (screenshot + VLM/OCR) when AX is insufficient.
- Add **shell/code**, **browser (Playwright)**, **AppleScript** tools; memory (vector store); confirmation gate for destructive actions.
- **Exit:** 5–10 step tasks succeed unattended ≥ 60%; graceful local-only degradation.

### Phase 3 — Native migration *(Weeks 10–14)*
- Swift/SwiftUI app shell + native HUD; AVFoundation audio; native ScreenCaptureKit, AX, CGEvent; code-sign + notarize.
- Port the loop (schemas are language-agnostic); keep local VLM/STT as an **MLX/Python sidecar** behind the tool interface.
- **Exit:** native app hits the < 800 ms voice target and passes Phase-2 task suite.

### Phase 4 — Realtime & polish *(Weeks 15–18)*
- Full-duplex realtime voice with **barge-in** and echo cancellation; latency tuning; **MCP** integrations; skill/macro memory; observability dashboard.
- Author **App Knowledge Packs** for headline pro apps (DaVinci Resolve scripting, Logic Pro key-commands, Office, IDEs) and wire up **Tier-0 delegation** to coding CLIs.
- **Exit:** natural interruptible conversation; external tools; metrics instrumented.

### Phase 5 — Hardening & beta *(ongoing)*
- Security review (incl. prompt-injection red-teaming), broaden app coverage, performance, private beta, auto-update.

---

## 11. Repository Structure

```
aether/
├── prototype/                  # Phase 1–2 Python
│   ├── core/
│   │   ├── world_model.py
│   │   ├── orchestrator.py     # plan / route / execute loop
│   │   ├── router.py           # model tier selection
│   │   └── policy.py           # safety gate
│   ├── perception/
│   │   ├── accessibility.py    # AX tree reader (pyobjc)
│   │   ├── screen.py           # ScreenCaptureKit / mss
│   │   ├── ocr.py              # Vision framework
│   │   └── audio_in.py         # mic + VAD + STT
│   ├── effectors/
│   │   ├── input.py            # CGEvent mouse/keyboard
│   │   ├── ax_actions.py
│   │   ├── applescript.py
│   │   ├── shell.py
│   │   └── browser.py          # Playwright
│   ├── voice/
│   │   ├── stt.py  └── tts.py
│   ├── tools/registry.py       # typed tool schemas + MCP client
│   ├── memory/store.py
│   └── app.py                  # menu-bar + HUD (rumps)
├── macos/                      # Phase 3+ native app
│   ├── Aether/                 # SwiftUI app
│   │   ├── Perception/  Effectors/  Voice/  Core/  HUD/
│   │   └── Aether.entitlements
│   └── Sidecar/                # MLX/Python ML helper
├── shared/
│   ├── tool_schemas/           # language-agnostic JSON contracts
│   └── prompts/
├── configs/router.yaml         # role → model-id mapping
└── docs/  (this spec)
```

---

## 12. Key Interfaces & Pseudocode

### 12.1 Uniform tool contract

```python
@dataclass
class ToolSpec:
    name: str
    json_schema: dict            # args the model must produce
    permission: str              # "screen" | "input" | "files" | "shell" | "network" | ...
    impact: str                  # "read" | "reversible" | "destructive"

class Tool(Protocol):
    spec: ToolSpec
    async def invoke(self, args: dict) -> "Observation": ...
```

### 12.2 Core loop (simplified)

```python
async def run(goal: str):
    plan = await planner.decompose(goal, world.snapshot())     # cloud, novel goals
    while not plan.done and not STOP.is_set():
        world.refresh()                                        # AX delta, transcript, state
        step = await router.next_action(goal, plan, world)     # local fast tier when possible
        if step.impact == "destructive" or policy.requires_confirm(step):
            if not await confirm_with_user(step):              # voice + HUD
                continue
        obs = await tools[step.name].invoke(step.args)         # act
        if not world.verify(step.expected, obs):               # observe / self-correct
            plan = await planner.replan(goal, world, failure=obs)
        await voice.narrate(step.summary)                      # streamed TTS
```

### 12.3 Native primitives (Swift sketches)

```swift
// Trust check for Accessibility
let trusted = AXIsProcessTrustedWithOptions(
    [kAXTrustedCheckOptionPrompt.takeRetainedValue(): true] as CFDictionary)

// Synthetic click at a point
func click(at p: CGPoint) {
    let down = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown,
                       mouseCursorPosition: p, mouseButton: .left)
    let up   = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp,
                       mouseCursorPosition: p, mouseButton: .left)
    down?.post(tap: .cghidEventTap); up?.post(tap: .cghidEventTap)
}

// Press a button found in the AX tree (preferred over pixel click)
func press(_ element: AXUIElement) {
    AXUIElementPerformAction(element, kAXPressAction as CFString)
}
```

```swift
// ScreenCaptureKit: capture the active display on demand
let content = try await SCShareableContent.current
let display = content.displays.first!
let cfg = SCStreamConfiguration(); cfg.width = display.width; cfg.height = display.height
let filter = SCContentFilter(display: display, excludingWindows: [])
// start an SCStream with cfg+filter; pull a CMSampleBuffer only when vision is needed
```

---

## 13. Risks & Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Agent unreliability on novel UIs | High | AX-first grounding; verify-after-act; self-correct; vision fallback; skill memory for known tasks |
| Vision-path latency/cost | Med | Use AX whenever possible; downscale/crop frames; cache; route to vision only on AX miss |
| **Prompt injection** from screen/web content | High | Treat perceived text as untrusted data; policy gate + confirmation on all high-impact actions; egress allow-list |
| Destructive mistakes | High | Mandatory confirmation; dry-run/diff; reversible-by-default; instant STOP; audit log |
| Permission friction (TCC) | Med | Guided first-run onboarding; clear rationale per permission; degrade gracefully if denied |
| App UI changes break flows | Med | Don't hardcode pixels; AX + semantic targets; re-plan on mismatch |
| Cloud dependency / privacy | Med | Local fast paths; local-only mode; explicit, minimal cloud calls; redaction |
| Apple API restrictions/changes | Med | Stick to public APIs (AX, ScreenCaptureKit, CGEvent); abstract effectors behind one interface |
| Cost blow-ups | Low–Med | Router keeps ~70–80% steps local; prompt/plan caching; per-task budget caps |
| Security attack surface (full control) | High | Least-privilege, signed helper, Keychain secrets, no-sandbox documented & contained, red-team |

---

## 14. Open Questions / Decisions to Make

1. **Default autonomy level** — how much can it do before asking? (Proposed: read freely, act reversibly with narration, confirm anything destructive.)
2. **Realtime voice provider** — OpenAI Realtime vs Gemini Live vs local-first; trade natural turn-taking vs privacy/cost.
3. **Local VLM hardware budget** — running a UI-TARS-class grounding model locally needs significant unified memory; cloud vision fallback otherwise.
4. **Browser strategy** — dedicated automation (Playwright/CDP) vs driving the user's existing browser via AX.
5. **Multi-display / multi-Space** handling and per-window capture scoping.
6. **Secure-field policy** — never type into password fields, or allow with explicit per-instance consent?
7. **Update & telemetry** — Sparkle auto-update; what (if any) anonymized metrics, opt-in.

---

## 15. Reference Open-Source Projects

Worth studying / borrowing from (verify license + maintenance before depending on any):

- **trycua/cua** — infrastructure/SDKs/sandboxes for computer-use agents incl. macOS.
- **simular-ai/Agent-S** — SOTA generalist-specialist computer-use framework.
- **OthersideAI/self-operating-computer** — canonical screenshot→action reference.
- **AmberSahdev/Open-Interface** — "control any computer with LLMs."
- **suitedaces/computer-agent** — desktop app driving mouse/keyboard/terminal/browser.
- **openinterpreter/01** — open-source voice interface for desktop control.
- **bytedance/UI-TARS** — purpose-built GUI grounding vision model (local 7B/72B).
- **sambuild04/screen-voice-agent** — macOS voice+screen agent (close to this concept).
- **Fazm** — accessibility-first macOS desktop agent (AX-first approach, like this spec).
- **ranpox/awesome-computer-use** — curated index to find more.

---

## 16. Appendix

### 16.1 Glossary

- **AX / Accessibility tree** — macOS's structured representation of UI elements; the cheapest reliable percept.
- **CGEvent** — Core Graphics API to synthesize OS-level mouse/keyboard input.
- **ScreenCaptureKit** — Apple's modern, efficient screen-capture framework.
- **TCC** — Transparency, Consent & Control; macOS's per-app privacy permission system.
- **VAD** — Voice Activity Detection; finds speech boundaries for low-latency turn-taking.
- **Dual-loop** — concurrent fast (local) and slow (cloud) reasoning loops sharing a world model.
- **MCP** — Model Context Protocol; standard interface for exposing external tools to the model.
- **Grounding** — mapping a described target ("the Send button") to an exact element/coordinate.

### 16.2 Key facts referenced (mid-2026)

- Conversational voice: sub-second first-audio is now table stakes; OpenAI Realtime ~300–500 ms, Gemini Live ~320–800 ms to first audio.
- Computer-use SOTA: Claude Opus 4.8 ~83% OSWorld-Verified (May 2026); Sonnet 4.6 ~72.5% (Feb 2026); Computer Use generally available.
- Open grounding: UI-TARS (ByteDance) ships local 7B/72B GUI models; Agent-S2 competitive on OSWorld.

### 16.3 Sources

- OpenAI — Advancing voice intelligence with new models in the API: https://openai.com/index/advancing-voice-intelligence-with-new-models-in-the-api/
- AssemblyAI — Best speech-to-speech voice agent API (2026): https://www.assemblyai.com/blog/best-speech-to-speech-voice-agent-api
- Google — Gemini Live API overview: https://ai.google.dev/gemini-api/docs/live-api
- Anthropic Claude models & computer use (2026 guide): https://www.nxcode.io/resources/news/claude-ai-complete-guide-models-pricing-features-2026
- OSWorld benchmark: https://os-world.github.io/
- UI-TARS (paper / repo): https://arxiv.org/abs/2501.12326 · https://github.com/bytedance/ui-tars
- Agent S2: https://arxiv.org/pdf/2504.00906
- trycua/cua: https://github.com/trycua/cua
- OthersideAI/self-operating-computer: https://github.com/OthersideAI/self-operating-computer
- openinterpreter/01: https://github.com/openinterpreter/01
- ranpox/awesome-computer-use: https://github.com/ranpox/awesome-computer-use
- DaVinci Resolve scripting API docs (unofficial mirror): https://deric.github.io/DaVinciResolve-API-Docs/
- Google Antigravity (agent-first IDE): https://blog.google/innovation-and-ai/technology/developers-tools/google-io-2026-developer-highlights/
- Best AI CLI tools 2026 (Codex/OpenCode/Claude Code/Kilo benchmarks): https://www.morphllm.com/best-ai-cli-tools-2026
- Kilo CLI 1.0 (VentureBeat): https://venturebeat.com/orchestration/kilo-cli-1-0-brings-open-source-vibe-coding-to-your-terminal-with-support

---

*End of specification — v0.1 draft. This is a living document; the model/provider slots in §7.2 and the open questions in §14 are expected to evolve.*


