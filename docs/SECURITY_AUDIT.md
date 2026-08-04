# Aether Security Self-Audit (Phase 12)

**Version:** 1.0.0-rc.1 · **Date:** June 2026 · **Scope:** Pre-GA hardening (NFR-5)

Structured checklist for the Aether codebase with findings and mitigations.

---

## Summary

| Area | Status | Notes |
|------|--------|-------|
| Prompt injection | ✅ Mitigated | Pattern scanner + policy gate + untrusted wrapping |
| Sidecar CORS | ✅ Hardened | Localhost-only by default (Phase 12) |
| Sidecar auth | ✅ Optional | `AETHER_SIDECAR_TOKEN` on mutating endpoints |
| Rate limiting | ✅ Added | Token bucket on `POST /run`, `/feedback` |
| Audit log | ✅ HMAC chain | Key in `data/.audit_hmac_key` or env |
| STOP / LLM cancel | ✅ Phase 12 | Abort in-flight HTTP on STOP |
| Delegation sandbox | ✅ Phase 11 | Env allowlist + workspace roots |
| Skill replay | ✅ Phase 11 | Confirmation gate + auth on `/skills/{id}/replay` |
| MCP SSRF | ✅ Mitigated | `allow_private_urls` default false; `url_safety.validate_outbound_url` |
| Plugin loading | ⚠️ Opt-in | `beta.plugins_enabled` — trust plugin code |
| Swift TCC | ℹ️ User grant | Permissions enforced by macOS |
| Shell classifier evasion | ✅ Phase 14 | De-obfuscation (`${IFS}`, var-assembly, quote-split) + cred-exfil product |
| AppleScript → shell | ✅ Phase 15 | `do shell script` literals routed through the shell gate |
| Browser SSRF / schemes | ✅ Phase 15 | `_url_is_dangerous`: metadata IPs, private ranges, `file:`/`javascript:` |
| UI input → RCE | ✅ Phase 16 | `FocusState` gates keystrokes by target surface, not payload |
| Fleet / delegation | ✅ Phase 16 | `spawn_agent(terminal)`, `send_to_agent`, `delegate_to_coder` are code-exec |
| Durable persistence | ✅ Phase 16 | `remember_fact` / `watch_app` confirm under untrusted content |
| **Untrusted-content source** | ⚠️ **Known gap** | Taint is set by `get_screen_context` ONLY, and is point-in-time. See below. |

---

## Checklist

### Authentication & transport

- [x] Sidecar binds `127.0.0.1` only
- [x] CORS defaults to localhost origins (set `AETHER_SIDECAR_CORS_ORIGINS=*` only in trusted dev)
- [x] Bearer token on `POST /run`, `/stop`, `/stt`, `/tts`, etc. when `AETHER_SIDECAR_TOKEN` set
- [x] Rate limiting on `/run` (12/min) and `/feedback` (6/min) per client IP
- [ ] mTLS / app attestation (post-GA)

**Finding:** Any local process can call the sidecar without a token.  
**Mitigation:** Set `AETHER_SIDECAR_TOKEN` for beta/GA; document in `BETA.md` and `docs/SUPPORT.md`.

### Prompt injection & tool abuse

- [x] High-severity goal patterns blocked (`policy.block_injection_goals`)
- [x] Medium/high patterns in tool args trigger confirmation
- [x] Screen/OCR content wrapped as untrusted
- [x] Red-team corpus: `tests/security/` (50+ cases)

**Finding:** Regex-only detection misses novel jailbreaks.  
**Mitigation:** Expand corpus; consider ML classifier post-GA; always use `--careful` for untrusted input.

### Path traversal & shell

- [x] `policy.allows_shell_path()` blocks paths outside `approved_file_roots`
- [x] Destructive shell patterns flagged
- [x] `capabilities.shell` toggle

**Finding:** Heuristic path detection may miss obfuscated paths.  
**Mitigation:** Careful mode; expand tests in `tests/security/test_red_team.py`.

### MCP & SSRF

- [x] MCP disabled by default
- [x] Stdio + SSE transports
- [x] Network allowlist affects browser tools
- [x] Block private IP ranges for MCP SSE URLs (`mcp.allow_private_urls: false` default; tests in `tests/security/test_mcp_ssrf.py`)

**Finding:** User could point MCP SSE at internal endpoints when `allow_private_urls: true`.  
**Mitigation:** Keep `allow_private_urls: false`; enable only for trusted local MCP servers; use `policy.network_allowlist` in careful deployments.

### Delegation (`delegate_to_coder`)

- [x] Subprocess env stripped to allowlist
- [x] Workspace constrained to `approved_file_roots`
- [x] Timeout tiers
- [x] Null-byte sanitization on prompts

**Finding:** Delegation still runs arbitrary CLIs with user PATH.  
**Mitigation:** Disable `delegation.enabled` or restrict to known agents; structured output only.

### Audit log & secrets

- [x] HMAC-signed append-only JSONL
- [x] `GET /audit/verify` tamper check
- [x] Secret redaction in policy and error responses
- [x] `AETHER_AUDIT_KEY` env override (Phase 12)

**Finding:** Default HMAC key stored in `data/.audit_hmac_key` on disk.  
**Mitigation (Keychain migration path):**

1. **Today:** Set `AETHER_AUDIT_KEY` in `.env` (32+ byte secret); restrict file permissions on `data/`.
2. **Phase 12+ (Swift bridge):** ✅ `AuditKeychain.swift` reads/writes key from macOS Keychain service `com.aether.audit`.
3. **Sidecar startup:** ✅ Python prefers Keychain → `AETHER_AUDIT_KEY` → file fallback (`GET /health` reports `audit.key_source`).
4. **Migration:** On first launch with Keychain available, move file key to Keychain and delete `data/.audit_hmac_key`.

### STOP (FR-26)

- [x] Global event checked before tool dispatch
- [x] In-flight LLM HTTP aborted via httpx client close
- [x] `stop_latency_ms` metric (budget 200 ms)
- [x] Swift reports client-side STOP latency

### Crash reporting

- [x] Opt-in `beta.crash_reporting: false` by default
- [x] `POST /crash-report` redacts secrets in stack traces
- [x] Stored locally in `data/crash_reports.jsonl`

---

## Test coverage

Run security suite:

```bash
python -m pytest tests/security/ -q
```

Categories: prompt injection, red-team, MCP SSRF/policy, skill replay, sidecar hardening (60 tests in `tests/security/`).

---

## Residual risks (accept for GA)

1. **Voice latency** — Cloud STT+LLM+TTS exceeds NFR-1 800 ms; documented product decision in `docs/GA_LAUNCH.md`.
2. **No hardware AEC** — Barge-in uses energy ducking only.
3. **Python effectors on hot path** — Native Swift migration ongoing.
4. **No formal third-party pentest** — Self-audit only for rc.1.
5. **Untrusted-content detection is narrow and point-in-time** (open, Phase 17).
   `Orchestrator._context_is_untrusted()` scans `world.ax_rendered`, which is
   written at exactly one site — the `get_screen_context` handler. Two consequences:
   - **Missing sources.** `browser_get_text` (reading a web page — the canonical
     injection vector), `get_app_context`, `analyze_screen`, `run_shell` stdout,
     `get_agent_output` and MCP results all carry attacker-controlled text into
     the model's context without setting the flag.
   - **Not sticky.** Re-snapshotting a clean screen clears the flag even though
     the injected text remains in the conversation, so
     `read injected page → open Terminal → re-snapshot → type payload` evades the
     Rule-of-Two blanket that Phases 14–16 depend on.

   This is the ceiling on every untrusted-gated defense in this document: the
   payload classifiers (`impact_of`) still apply, but the blanket that covers
   novel/obfuscated payloads may not fire. Calibration is the hard part —
   measured, ~29% of realistic benign content (ordinary email, code comments,
   news prose) trips `scan_injection`, so a naive sticky flag over broad sources
   would confirm most of a normal run and train click-through.
6. **Sub-agent policy does not propagate.** `delegate_to_coder` / `spawn_agent`
   launch third-party CLIs that inherit none of `careful`, `capabilities`,
   `network_allowlist` or `approved_file_roots`. Phase 16 forces a confirm at the
   spawn hop under untrusted content; the child itself is ungoverned.
7. **`allows_shell_path` covers only `run_shell`.** A terminal-session spawn or
   steer reaches a shell without the `approved_file_roots` check.

---

*Review after each release. Update findings when architecture changes.*
