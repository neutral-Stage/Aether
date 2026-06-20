# Aether Private Beta → GA

**Version:** see `VERSION` (currently `1.0.0-rc.1`)

Aether is an experimental macOS AI agent that controls your real computer. This beta is for early testers who understand the risks and will provide feedback.

**Graduating to GA:** Phases 0–12 are complete. Follow [`docs/GA_LAUNCH.md`](docs/GA_LAUNCH.md) for the release checklist. Install the signed DMG when available; set `AETHER_SIDECAR_TOKEN` for production sidecar auth.

## Install (dev / beta)

```bash
cd "/path/to/Aether"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-sidecar.txt
cp .env.example .env   # if present; set ANTHROPIC_API_KEY
./macos/run-dev.sh
```

In another terminal or via the menu bar app:

```bash
cd macos/Aether && swift run
```

## Required permissions

| Permission | Why |
|---|---|
| **Accessibility** | Read UI trees and synthesize clicks |
| **Screen Recording** | Screenshots / ScreenCaptureKit |
| **Microphone** | Push-to-talk and barge-in |
| **Input Monitoring** | Global STOP (`⌃⇧S`) and PTT (`⌃Space` hold) |

Grant these in **System Settings → Privacy & Security**. Restart the app after enabling.

## Security (Phase 5)

- **Prompt-injection detection** blocks high-risk patterns in user goals; flags medium-risk in tool args.
- **Audit log** at `data/audit.jsonl` — append-only, HMAC-signed chain. Verify: `curl http://127.0.0.1:8765/audit/verify`
- **`--careful` / `careful: true`** confirms before every non-read tool; enforces network allowlist when configured.
- Perceived screen text is wrapped as **untrusted data**, never instructions.

## Beta feature flags (`config.yaml` → `beta:`)

| Flag | Default | Description |
|---|---|---|
| `continuous_screen_stream` | `false` | Continuous ScreenCaptureKit (Swift) |
| `ambient_listening` | `false` | Always-on mic / wake word |
| `auto_update_check` | `true` | Swift app checks release feed on launch |

## Known issues

- Global hotkeys require **Input Monitoring**; may not fire until permission granted.
- Echo cancellation is duck-only (no full AEC).
- Update feed URL is a placeholder until you publish GitHub releases.
- Cloud STT/TTS require API keys; Apple Speech works offline for STT in the Swift shell.

## Feedback

Use the in-app feedback field (stored via `POST /feedback`) or open a GitHub issue.  
User guide: [`docs/SUPPORT.md`](docs/SUPPORT.md)

## Health check

```bash
curl -s http://127.0.0.1:8765/health | python -m json.tool
```

## Sidecar update (manual)

```bash
git pull
pip install -r requirements.txt -r requirements-sidecar.txt
# restart sidecar / run-dev.sh
```

Or run: `python scripts/check_update.py`
