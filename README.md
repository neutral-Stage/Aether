# Aether — GA Release Candidate (1.0.0-rc.1)

A working Python agent for macOS described in
`Aether_macOS_AI_Agent_Engineering_Spec.md`. **Phases 0–12 are complete** — Aether
is **GA-ready** as release candidate `1.0.0-rc.1`. See
**[`docs/GA_LAUNCH.md`](docs/GA_LAUNCH.md)** for the launch checklist and known
limitations.

> ⚠️ Aether controls your real Mac (mouse, keyboard, apps, shell). Run it on a
> machine you're comfortable experimenting on. Use **STOP** (HUD button,
> `Ctrl+Shift+S`, double-tap Escape, or say "stop") or **Ctrl-C** to halt.
> Use `--careful` to confirm every action. See **`BETA.md`** for beta install and
> **`docs/SUPPORT.md`** for troubleshooting.

---

## GA readiness (Phase 12)

| Area | Location |
|------|----------|
| Security self-audit | [`docs/SECURITY_AUDIT.md`](docs/SECURITY_AUDIT.md) |
| Support runbook | [`docs/RUNBOOK.md`](docs/RUNBOOK.md) |
| User troubleshooting | [`docs/SUPPORT.md`](docs/SUPPORT.md) |
| Plugin SDK | [`docs/PLUGIN_SDK.md`](docs/PLUGIN_SDK.md) |
| Launch checklist | [`docs/GA_LAUNCH.md`](docs/GA_LAUNCH.md) |
| Security tests (50+) | `tests/security/` |
| STOP + LLM cancel | `aether/core/stop.py`, `tests/unit/test_stop_cancel.py` |
| Plugin loader | `aether/plugins/loader.py`, `plugins/example_hello/` |

```bash
make ci                    # compile + test + benchmark + validate-packs + swift
python -m pytest tests/security/ -q
```

---

## What's in Phase 5

| Component | Location | Notes |
|---|---|---|
| Prompt-injection defense | `aether/core/security.py` | Pattern scan; untrusted context wrapping |
| Signed audit log | `aether/core/audit_log.py` | `data/audit.jsonl` HMAC hash chain |
| Security tests | `tests/security/` | Red-team attack strings |
| Knowledge packs | `aether/knowledge/packs/` | Slack, Chrome, Terminal, Notes, Calendar |
| Performance budgets | `aether/core/metrics.py` | Slow-path warnings; AX TTL cache |
| Health + audit API | `sidecar/server.py` | `GET /health`, `GET /audit/verify` |
| Global PTT | `macos/.../PTTHotkeyController.swift` | Hold `⌃Space` system-wide |
| Update checker | `macos/.../UpdateChecker.swift` | GitHub releases JSON (configurable) |
| Signing guide | `macos/SIGNING.md` | Developer ID + notarization |

### Latency targets (NFR)

| Path | Budget |
|---|---|
| AX refresh | p50 &lt; 50 ms (warn if &gt; 50 ms) |
| Percept refresh (cached) | &lt; 100 ms |
| Agent step | warn if &gt; 3000 ms |
| Tool call | warn if &gt; 2000 ms |

```bash
# Audit log verification
curl -s http://127.0.0.1:8765/audit/verify | python -m json.tool

# Security tests
python -m pytest tests/security/ -q

# Version / update check
python scripts/check_update.py
```

---

## What's in Phase 4

| Component | Location | Notes |
|---|---|---|
| Barge-in voice | `macos/.../Voice/VoicePipeline.swift` | Mic open during TTS; energy VAD + partial STT stops speech |
| Live MCP client | `aether/tools/mcp_client.py` | Stdio JSON-RPC; registers `mcp_*` tools in registry |
| Skill memory | `aether/memory/skills.py` | Distills successful traces → reusable parameterized skills |
| Observability | `aether/core/metrics.py` | `GET /metrics` JSON + `GET /dashboard` HTML |
| Tier-0 delegation | `aether/tools/delegation.py` | `delegate_to_coder` tool (claude/codex/opencode/cursor) |
| Knowledge packs | `aether/knowledge/packs/*.yaml` | Resolve, Logic Pro, Office, VS Code/Cursor |
| ScreenCaptureKit | `macos/.../Perception/ScreenCapture.swift` | Single-frame PNG capture |

**Exit criterion (from the spec):** natural interruptible conversation; external
tools via MCP; metrics instrumented.

---

## Phase 4 quick start

```bash
source .venv/bin/activate
pip install -r requirements.txt -r requirements-sidecar.txt
./macos/run-dev.sh
```

```bash
# Observability
curl -s http://127.0.0.1:8765/metrics | python -m json.tool
open http://127.0.0.1:8765/dashboard

# Enable MCP (config.yaml) — example filesystem server:
# mcp.enabled: true
# servers: [{ name: fs, command: npx, args: ["-y", "@modelcontextprotocol/server-filesystem", "/Users/you"], enabled: true }]
```

### Barge-in (Swift shell)

- Toggle **Barge-in** in the main window (on by default).
- While Aether speaks, talk over it — TTS ducks then stops immediately.
- Partial transcript appears on the HUD during interruption.

### Skill memory

After a successful multi-step run, the orchestrator distills the tool trace into
`data/skills.db`. Similar skills are injected into the planner system prompt on
future matching goals.

### Tier-0 coding delegation

```
"Use delegate_to_coder to add unit tests for the auth module in ~/Projects/myapp"
```

Requires a coding CLI on `PATH` (`claude`, `codex`, `opencode`, or `cursor`).

---

## What's in Phase 3 (still present)

| Component | Location | Notes |
|---|---|---|
| Shared tool contracts | `shared/tool_schemas/` | JSON schemas + manifest |
| Python sidecar | `sidecar/server.py` | SSE `/run`, `/stop`, `/metrics`, `/dashboard` |
| Native Swift shell | `macos/Aether/` | MenuBarExtra, HUD, voice, onboarding |
| Phase 2 agent | `aether/`, `python run.py` | Unchanged CLI path |

---

## What's in Phase 2 (still present)

| Component | Module | Notes |
|---|---|---|
| Model router | `aether/core/router.py` | local_fast / cloud_frontier / vision tiers |
| Router config | `configs/router.yaml` | Role → model endpoint mapping |
| Dual-loop orchestrator | `aether/core/orchestrator.py` | Fast local + slow cloud share world model |
| Verify-after-act | `aether/core/world_model.py` | AX delta checks, failure tracking |
| Vision / OCR | `aether/perception/ocr.py` | Apple Vision via pyobjc |
| Browser automation | `aether/effectors/browser.py` | Playwright Chromium tools |
| Long-term memory | `aether/memory/store.py` | SQLite + hash embeddings |
| MCP client | `aether/tools/mcp_client.py` | Live stdio MCP (disabled by default) |
| Skill memory | `aether/memory/skills.py` | Learn macros from successful traces |
| Observability | `aether/core/metrics.py` | Per-step/tool latency metrics |
| Tier-0 delegation | `delegate_to_coder` tool | Supervised coding CLI subprocess |
| Expanded policy | `aether/core/policy.py` | Destructive ops, path scope, secret redaction |
| Phase 1 foundations | world model, registry, HUD, STOP, knowledge packs | Still present |

**Exit criterion (from the spec):** 5–10 step tasks succeed unattended ≥ 60%;
graceful local-only degradation when cloud or Playwright unavailable.

---

## Requirements

- macOS 13+ (Apple Silicon recommended)
- Python 3.10+
- **Anthropic API key** for cloud frontier (or any provider key in `configs/router.yaml`; or `--local-only` with Ollama)
- **Ollama** optional for local fast loop: `brew install ollama && ollama pull llama3.2:3b`
- **Playwright** optional for browser tools: `pip install playwright && playwright install chromium`
- For voice: `brew install portaudio` (for `sounddevice`).

---

## Setup

```bash
cd "Aether"

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

# Optional: browser automation
playwright install chromium

# Optional: local fast model
ollama pull llama3.2:3b
ollama serve   # if not already running

cp .env.example .env
# Add at least one cloud provider API key (see configs/router.yaml providers)
```

### LLM providers

Edit `configs/router.yaml` to pick the active cloud backend:

```yaml
roles:
  cloud_frontier:
    provider: zai          # glm-5-turbo supervisor (default)
  vision:
    provider: zai_vision   # glm-5v-turbo vision (GLM-5V)
```

Other providers: `anthropic`, `openrouter`, `groq`, `openai`, `kilo`, `kie_gemini`, `google`, `fireworks`.

### Voice (Groq STT + TTS)

`config.yaml` defaults to Groq for speech:

```yaml
voice:
  stt: groq
  stt_model: whisper-large-v3-turbo
  tts: groq
  tts_model: canopylabs/orpheus-v1-english
  tts_voice: troy
```

Set `GROQ_API_KEY` in `.env`. The macOS shell calls sidecar `/stt` and `/tts` when configured for Groq.
Use `tts: macos` to fall back to AVSpeechSynthesizer offline.

Provider templates and env vars are documented in `.env.example`. Test connectivity:

```bash
python scripts/test_providers.py --dry-run
python scripts/test_providers.py --provider openrouter
```

See `docs/TESTING.md` for a prioritized validation checklist.

### Configure local model

Edit `configs/router.yaml`:

```yaml
roles:
  local_fast:
    endpoint: http://localhost:11434/api/chat
    model: llama3.2:3b
```

Ollama, llama.cpp server, or MLX HTTP APIs with compatible `/api/chat` work.

### macOS permissions

In **System Settings → Privacy & Security**:

1. **Accessibility** → enable your terminal (required)
2. **Screen Recording** → enable your terminal (screenshots, OCR)
3. **Microphone** → allow when prompted (voice only)

---

## Run Phase 2

```bash
# Standard (cloud frontier + local fast when AX is good)
python run.py --text "Open Safari and go to apple.com"

# Browser automation (Playwright)
python run.py --text "Use the browser to open https://example.com and read the h1"

# Vision fallback when AX is poor
python run.py --text "Take a screenshot and analyze what's on screen"

# Remember a preference for future runs
python run.py --text "Remember that I prefer Safari over Chrome for web tasks"

# Local-only mode (Ollama; cloud fallback on hard steps if key set)
python run.py --local-only --text "Open Finder and go to Downloads"

# Careful mode (confirm destructive actions + network allow-list)
python run.py --careful --text "Run git status in my project"

# Interactive REPL
python run.py
```

### STOP controls

| Method | Action |
|---|---|
| HUD **STOP** button | Halts the current task |
| `Ctrl+Shift+S` | Global hotkey (requires `pynput`) |
| Double-tap **Escape** | Alternative hotkey |
| **Ctrl-C** | Kills the process |

---

## Architecture (Phase 2 dual-loop)

```
command (voice/text)
      │
      ▼
perceive ──► WorldModel.refresh()
      │
      ▼
route ──► Router (local_fast | cloud_frontier | vision)
      │         ├── fast loop: Ollama for reflexive steps
      │         └── slow loop: Claude for planning / recovery
      ▼
reason ──► LLM + memory + knowledge packs (+ OCR if vision tier)
      │
      ▼
policy gate ──► confirm destructive / redact secrets
      │
      ▼
act ──► Registry (AX → AppleScript → browser → shell)
      │
      ▼
verify ──► AX delta check ──► self-correct on mismatch
      │
      ▼
memory ──► store successful task traces
```

---

## New tools (Phase 2)

| Tool | Purpose |
|---|---|
| `analyze_screen` | OCR/vision when AX insufficient |
| `remember_fact` | Store preference/quirk in long-term memory |
| `browser_navigate` | Playwright → URL |
| `browser_click` | CSS selector click |
| `browser_fill` | Form fill |
| `browser_get_text` | Read DOM text |
| `browser_screenshot` | Viewport capture |
| `delegate_to_coder` | Tier-0 supervised coding CLI (claude/codex/…) |
| `mcp_*` | Dynamic tools from enabled MCP servers |

---

## Safety

- Expanded destructive detection: `git push`, send/delete patterns, payments.
- File/shell scope: `policy.approved_file_roots` in `config.yaml`.
- Network allow-list in careful mode: `policy.network_allowlist`.
- Password/secure AX fields redacted before cloud prompts.
- STOP halts between tool steps.

---

## Known limitations (Phase 4)

- Barge-in uses energy threshold + partial STT — not full acoustic echo cancellation.
- MCP servers must speak stdio JSON-RPC; SSE transport not yet supported.
- Skill distillation is heuristic (hash embeddings, not semantic clustering).
- `delegate_to_coder` requires the target CLI installed and authenticated.
- ScreenCaptureKit captures single frames, not continuous streaming.
- Global PTT hotkey requires Input Monitoring (see `macos/README.md`); README Phase 4 note is stale.
- CLI `python run.py` still uses tkinter HUD; native HUD is the Swift app.

### Phase 2 limitations (still apply)

- Local fast model tool-calling uses JSON-in-text parsing (less reliable than native Anthropic tools).
- Playwright runs headless Chromium, not the user's Safari/Chrome session.
- OCR coordinates are normalized; pixel clicks from OCR need scaling.
- Hash embeddings for memory are lightweight, not semantic-quality.

---

## Verification

```bash
python -m compileall -q aether sidecar
python -c "from aether.core.orchestrator import Agent"
curl -s http://127.0.0.1:8765/health   # with sidecar running
curl -s http://127.0.0.1:8765/metrics
cd macos/Aether && swift build
```

---

## Roadmap

Full improvement plan, gap analysis, and phased roadmap (Phases 6–12): **[`docs/ROADMAP.md`](docs/ROADMAP.md)**.

Highlights: CI/test suite, wake word & ambient mode, continuous ScreenCaptureKit,
AEC, MCP SSE, Sparkle distribution, and GA hardening.
