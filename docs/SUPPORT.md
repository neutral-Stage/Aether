# Aether Support & Troubleshooting

**For:** Beta and GA users · **Version:** 1.0.0-rc.1

---

## Getting help

1. Check this guide and `BETA.md`
2. Run health check: `curl -s http://127.0.0.1:8765/health`
3. File feedback via the app (stored in `data/feedback.jsonl`) or GitHub Issues

---

## Installation

```bash
cd "/path/to/Aether"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-sidecar.txt
cp .env.example .env   # add API keys
./macos/run-dev.sh
# separate terminal:
cd macos/Aether && swift run
```

---

## Permissions (required)

| Permission | Used for |
|------------|----------|
| Accessibility | UI tree + clicks |
| Screen Recording | Screenshots / screen stream |
| Microphone | Voice input |
| Input Monitoring | Global STOP (⌃⇧S) and PTT (⌃Space) |

Grant in **System Settings → Privacy & Security**, then restart Aether.

---

## Common issues

### "Sidecar offline" in HUD

- Start `./macos/run-dev.sh`
- Check port 8765: `curl http://127.0.0.1:8765/health`

### Agent won't run

- Set at least one LLM API key in `.env` (see `configs/router.yaml`)
- Or enable `local_only: true` with Ollama running locally

### STOP doesn't stop quickly

- Press ⌃⇧S or HUD STOP button
- Say "stop" during voice
- If hung > 2 s, quit app and `POST /stop` via curl

### Unauthorized (401)

Set matching token in `.env` and Swift (if configured):

```bash
export AETHER_SIDECAR_TOKEN=your-secret
```

### Rate limited (429)

Wait a minute — limits protect against runaway local scripts on `/run` and `/feedback`.

### Voice is slow

Cloud STT + LLM + TTS typically takes 1–3+ seconds. For lower latency:

- Enable `beta.realtime_voice: true` (requires OpenAI key)
- See `docs/VOICE.md`

### Plugins not loading

Set `beta.plugins_enabled: true` and place plugins in `~/.aether/plugins/`.  
See `docs/PLUGIN_SDK.md`.

### Crash reporting

Off by default. Enable with `beta.crash_reporting: true` to send anonymous stacks to local `data/crash_reports.jsonl` only (127.0.0.1).

---

## Safe use

- Run on a machine you trust Aether to control
- Use **careful mode** for sensitive workflows
- Review actions in the HUD before confirming
- Use STOP immediately if behavior is unexpected

---

## Graduating from beta

See `docs/GA_LAUNCH.md` for the full release checklist.  
When GA ships: install signed DMG, enable auto-update (Sparkle), set sidecar token.

---

*Operators: see `docs/RUNBOOK.md`.*
