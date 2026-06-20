# Aether Support Runbook

**Audience:** Operators and support engineers · **Version:** 1.0.0-rc.1

Common failures, diagnostics, and recovery steps for the Aether sidecar and macOS shell.

---

## Quick diagnostics

```bash
# Health + audit chain
curl -s http://127.0.0.1:8765/health | python -m json.tool

# Metrics / STOP latency
curl -s http://127.0.0.1:8765/metrics | python -m json.tool

# Verify audit log integrity
curl -s http://127.0.0.1:8765/audit/verify
```

---

## Sidecar won't start

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Address already in use` | Port 8765 taken | `lsof -i :8765` → kill stale process |
| Import errors | Missing deps | `pip install -r requirements.txt -r requirements-sidecar.txt` |
| Config parse error | Invalid YAML | Validate against `configs/config.schema.json` |

---

## Agent run fails immediately

| Symptom | Cause | Fix |
|---------|-------|-----|
| HTTP 400 "No cloud LLM API key" | Missing `.env` keys | Set provider key per `configs/router.yaml` or use `local_only: true` |
| HTTP 401 | Token mismatch | Set `Authorization: Bearer $AETHER_SIDECAR_TOKEN` in Swift/env |
| HTTP 409 | Concurrent run | `POST /stop` then retry |
| HTTP 429 | Rate limit | Wait per `Retry-After` header |
| HTTP 500 structured error | Internal fault | Check sidecar logs; see `data/crash_reports.jsonl` if crash reporting enabled |

---

## STOP not working

1. Confirm Input Monitoring permission for the Swift app.
2. Test sidecar directly: `curl -X POST http://127.0.0.1:8765/stop`
3. Check `stop_latency_ms` in metrics — budget is 200 ms.
4. If stuck mid-LLM call, ensure sidecar is Phase 12+ (LLM abort on STOP).

---

## Voice issues

| Symptom | Fix |
|---------|-----|
| No transcription | Grant Microphone; set `GROQ_API_KEY` or use Apple Speech |
| High latency | Expected with cloud pipeline; see `docs/VOICE.md` |
| Barge-in false triggers | Lower `vad_energy_threshold`; disable ambient mode |

---

## MCP servers hang or fail

1. `GET /config/mcp` — check server status.
2. `POST /config/mcp/reload` — reset sessions.
3. Disable problematic server in `config.yaml` → `mcp.servers[].enabled: false`.
4. Prefer stdio for local tools; SSE only for trusted remote hosts.

---

## Audit log tamper / verify fails

1. Do not edit `data/audit.jsonl` manually.
2. Set stable `AETHER_AUDIT_KEY` before first run in production.
3. If key lost, chain cannot be verified — archive old log and start fresh.

---

## Plugin errors

1. Plugins load only when `beta.plugins_enabled: true` or `plugins.enabled: true`.
2. Check sidecar logs for `Failed to load plugin`.
3. Validate `plugin.yaml` + `register(registry)` in `~/.aether/plugins/` or `plugins/`.

---

## Crash reports (opt-in)

When `beta.crash_reporting: true`, reports append to `data/crash_reports.jsonl`.  
Secrets are redacted server-side. Disable for privacy-sensitive deployments.

---

## Escalation data to collect

1. `VERSION` file contents
2. `curl /health` and `/metrics` JSON
3. Last 20 lines of `data/audit.jsonl` (redact PII)
4. `config.yaml` (redact keys)
5. Steps to reproduce + expected behavior

---

*See also `docs/SUPPORT.md` for end-user troubleshooting.*
