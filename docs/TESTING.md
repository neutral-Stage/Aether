# Aether — Testing guide

Automated and manual test commands for release candidates and local development.

## Automated tests (Phase 6+)

| Suite | Command | Notes |
|-------|---------|-------|
| Full suite | `make test` or `python -m pytest tests/ -q` | Unit + integration + security + benchmark (~175+ tests) |
| Unit only | `make test-unit` | Router, policy, registry, world model, orchestrator smoke |
| Integration | `make test-integration` | Sidecar HTTP via FastAPI TestClient |
| Security | `python -m pytest tests/security/ -q` | 50+ cases — see paths below |
| Benchmark (mock) | `make benchmark` | Scores 10 scripted task traces (no LLM) |
| Benchmark (live) | `python scripts/benchmark_tasks.py --sidecar http://127.0.0.1:8765` | Requires running sidecar + API keys |
| CI locally | `make ci` | compileall + ruff + pytest + validate-packs + mock benchmark + swift build/test |

### Security test paths

| File | Focus |
|------|--------|
| `tests/security/test_prompt_injection.py` | Goal/context injection patterns |
| `tests/security/test_red_team.py` | Path traversal, shell, sidecar hardening |
| `tests/security/test_mcp_ssrf.py` | MCP SSE private URL blocking |
| `tests/security/test_mcp_policy.py` | MCP tool impact / policy integration |
| `tests/security/test_skill_replay.py` | Skill replay confirmation + feedback auth |

### Compile check

```bash
python -m compileall -q aether sidecar tests
```

### Swift build & test

```bash
cd macos/Aether && swift build && swift test
```

Swift XCTest runs in GitHub Actions (`.github/workflows/ci.yml`).

### Sidecar auth (optional)

Set `AETHER_SIDECAR_TOKEN` in `.env` to require `Authorization: Bearer <token>` on mutating endpoints and on `GET /metrics` / `GET /dashboard` when a token is configured. `/health`, `/audit/verify`, and `/config/voice` stay open for liveness probes.

---

## P0 — Security & core agent loop

Run these first on every release candidate.

| Test | How | Pass criteria |
|------|-----|---------------|
| Prompt injection suite | `python -m pytest tests/security/ -q` | All tests green |
| Audit log chain | `curl -s http://127.0.0.1:8765/audit/verify` | `ok: true` |
| STOP halts run | HUD STOP / `Ctrl+Shift+S` / double-Escape mid-task | Agent stops between tool steps |
| Policy gate (careful) | `python run.py --careful --text "delete all files"` | Destructive action blocked or confirmed |
| Secret redaction | Inject fake `api_key=sk-…` in screen context | Not echoed verbatim in cloud prompts (check logs) |

## P1 — Phase 4/5 features (not fully validated)

| Feature | What to test | Known gaps |
|---------|--------------|------------|
| **Barge-in** | Swift app: speak over TTS, verify duck + stop | Energy VAD only — no full AEC |
| **Global PTT** | Hold `⌃Space` outside Aether window | Requires Input Monitoring permission |
| **MCP tools** | Enable `mcp.servers` in `config.yaml`, call `mcp_*` tool | Stdio + SSE; private URLs blocked unless `allow_private_urls` |
| **Skill memory** | Multi-step success → check `data/skills.db`, repeat similar goal | Heuristic distillation, not semantic clustering |
| **Dashboard / metrics** | `open http://127.0.0.1:8765/dashboard`, run a task | Requires Bearer token when `AETHER_SIDECAR_TOKEN` set |
| **delegate_to_coder** | Goal referencing `delegate_to_coder` with CLI on PATH | Gated by `delegation.enabled`; needs CLI on PATH |
| **Knowledge packs** | Tasks in Mail, Safari, VS Code, Slack apps | Pack coverage varies by app state |
| **Injection tests** | Extend `tests/security/` with new attack strings from production logs | — |
| **Vision / OCR** | Task when AX tree empty: `analyze_screen` | OCR coords normalized, not pixel-perfect |

## P2 — Router & providers (test before switching cloud backend)

| Test | How |
|------|-----|
| Provider connectivity | `python scripts/test_providers.py --dry-run` then without `--dry-run` |
| Anthropic path (default) | `cloud_frontier.provider: anthropic` + `ANTHROPIC_API_KEY` |
| OpenAI-compatible swap | Change `provider:` in `configs/router.yaml`, re-run simple task |
| Local fast loop | Ollama running + reflexive task (`open_app`, `click`) |
| Local-only fallback | `--local-only` without cloud key; hard step should error gracefully |
| Vision tier | Force AX miss (empty desktop) → vision route in metrics |
| Failover chain | `python -m pytest tests/unit/test_router_failover.py -q` | `failover_providers` order in `configs/router.yaml` |

## P3 — macOS shell & beta infra

| Item | Status | Test |
|------|--------|------|
| Sparkle auto-update | **Stub** — `SparkleUpdateController.swift` + GitHub JSON fallback | `python scripts/check_update.py` |
| ScreenCaptureKit stream | **Single frame only** — `continuous_screen_stream: false` | Capture one frame via Swift shell |
| AEC (acoustic echo cancellation) | **Not implemented** | Barge-in may false-trigger near speakers |
| Update feed URL | `AETHER_UPDATE_FEED_URL` or `AETHER_GITHUB_REPO` env; sidecar `beta.update_feed_url` | Set real feed before production |

## P4 — Regression smoke (5 minutes)

```bash
source .venv/bin/activate
make ci
# or:
python -m compileall -q aether sidecar tests
ruff check aether sidecar tests
python -m pytest tests/ -q
python scripts/validate_packs.py
python scripts/benchmark_tasks.py --mock
cd macos/Aether && swift build && swift test
./macos/run-dev.sh   # optional: native HUD + voice
python run.py --text "Open Finder"
curl -s http://127.0.0.1:8765/health | python -m json.tool
```

## Recommended order for your next session

1. `pytest tests/security/` + audit verify  
2. Sidecar dashboard while running a 5-step task  
3. Barge-in + global PTT in Swift app (permissions onboarding)  
4. MCP server (filesystem) one read tool  
5. `delegate_to_coder` with your preferred CLI  
6. `scripts/test_providers.py` for each API key you plan to use  
7. Switch `cloud_frontier.provider` and repeat a simple goal  
