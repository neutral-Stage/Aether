# Aether GA Launch Checklist

**Target:** 1.0.0 GA · **Current:** 1.0.0-rc.1 (release candidate)

---

## Release candidate status

Phases 0–12 complete. Aether is **GA-ready** with documented exceptions below.

---

## Pre-release checklist

### Build & sign

- [ ] `make ci` green (Python + Swift + benchmark + validate-packs) — run before each release
- [x] GitHub CI workflow (`.github/workflows/ci.yml`): pytest, ruff, Swift test, validate-packs, mock benchmark
- [ ] `python -m pytest tests/security/ -q` — 50+ security cases (automated in CI)
- [ ] Developer ID sign + notarize (`macos/scripts/sign-and-notarize.sh`)
- [ ] DMG build (`macos/scripts/build-dmg.sh`)
- [ ] Sparkle appcast hosted

### Security

- [x] Optional `AETHER_SIDECAR_TOKEN` on mutating endpoints + Swift `sidecarBearerToken` bridge
- [x] Localhost-only CORS default (`sidecar/auth.py`); production must not set `AETHER_SIDECAR_CORS_ORIGINS=*`
- [ ] `AETHER_SIDECAR_TOKEN` set for beta/GA builds (deployment step)
- [ ] `AETHER_AUDIT_KEY` set (or Keychain migration planned)
- [ ] Review `docs/SECURITY_AUDIT.md` residual risks

### Configuration

- [x] `delegation.enabled` config gate (tool omitted when false)
- [x] `mcp.allow_private_urls` SSRF guard (default false)
- [x] `beta.native_effectors` gates Swift effector HTTP bridge
- [ ] `config.yaml` reviewed — risky beta flags off by default
- [ ] API keys in `.env` only, not committed
- [ ] `delegation.enabled` reviewed per deployment

### Documentation

- [ ] `README.md` links here
- [ ] `BETA.md` graduation note
- [ ] `docs/SUPPORT.md` published
- [ ] `docs/RUNBOOK.md` for operators

### Manual QA

- [ ] PTT, command bar (⌥Space), text window all work
- [ ] STOP < 200 ms (HUD + ⌃⇧S)
- [ ] Careful mode confirms destructive actions
- [ ] Onboarding + TCC permissions flow
- [ ] 10 external beta users (Phase 9 exit criterion)

---

## Known limitations (ship with docs)

| Item | Decision |
|------|----------|
| **NFR-1 voice p50 < 800 ms** | **Not met** with default cloud pipeline. Product decision: ship GA with cloud voice; document local-only / Realtime path (`beta.realtime_voice`) as fast path. Target < 2 s p50 interim. |
| Hardware AEC | Mic gate + duck only; full AEC post-GA |
| Wake word | Energy stub; Porcupine production quality TBD |
| MCP | User-configured; SSRF risk mitigated by docs + allowlist |
| Parallel sub-agents | Not implemented — deferred post-GA |
| Plugins | Opt-in; trusted source only |
| Swift effectors | `beta.native_effectors: false` by default |

---

## Versioning

| File | Value |
|------|-------|
| `VERSION` | `1.0.0-rc.1` → `1.0.0` at GA |
| `config.yaml` `version` | Match `VERSION` |
| `aether/__init__.py` | Match `VERSION` |

---

## Launch day

1. Tag `v1.0.0` on GitHub
2. Upload signed DMG + release notes
3. Update Sparkle appcast
4. Announce with link to `docs/SUPPORT.md`
5. Monitor `data/feedback.jsonl` and metrics dashboard

---

## Post-GA priorities

1. Voice latency (Realtime / local STT path)
2. Keychain audit key bridge
3. Formal security review
4. Live benchmark ≥ 60% task success
5. Plugin marketplace
