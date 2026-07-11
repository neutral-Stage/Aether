# First real run

Aether is heavily tested, but tests use mocks/fixtures — this is the checklist
to run it against reality for the first time. Do these in order; each step has a
diagnostic so a failure tells you what's wrong.

## 1. Preflight — `aether doctor`

```bash
python -m aether.app --doctor    # or: make doctor
```

It checks Python, sidecar deps, macOS perception (pyobjc), an LLM backend (a
provider key or Ollama), coding CLIs on PATH, git, and the Accessibility /
Screen-Recording permissions. Each ✗ prints the exact fix. Resolve every ✗
before continuing (a `!` is a degraded-but-usable warning).

## 2. Keys

Either export a provider key (`ANTHROPIC_API_KEY=…`) for CLI use, or — in the
Swift app — enter it under **Settings → API Keys** (stored in the Keychain; the
app injects it into the sidecar it supervises). At least one cloud key is needed
for the frontier tier; `--local-only` works with Ollama.

## 3. Permissions (macOS TCC)

Grant the process running Aether (Terminal/iTerm for dev, or Aether.app):
- **Accessibility** — required, to see + control apps.
- **Screen Recording** — for the vision fallback.
- **Microphone** — for voice.

System Settings → Privacy & Security → each category. Screen Recording takes
effect after a restart of the app.

## 4. Coding CLIs (for the fleet)

Put at least one of `claude` / `codex` / `opencode` / `cursor` on PATH so
`spawn_agent`, `spawn_graph`, and `delegate_to_coder` work. `aether doctor`
reports which are found.

## 5. Smoke the running stack — `live_smoke.py`

```bash
python -m sidecar.server &                     # or let the Swift app supervise it
python scripts/live_smoke.py                   # keyless: health, /catalog, /runs, /events
python scripts/live_smoke.py --with-agents     # + real fleet spawn + STOP latency (needs a CLI + key)
```

`--with-agents` spawns a real `claude` session, then fires STOP and asserts it
dies in < 3s — the Phase-9 kill-switch against reality, not a fake.

## 6. A real command

From the app: say or type "open Safari and go to apple.com", then something with
the fleet: **"spawn a Claude Code agent to fix the failing tests in ~/proj and
tell me when it's done."** Watch the HUD + `GET /fleet`. Hit STOP to confirm it
halts.

## Distribution (see `macos/SIGNING.md`)

A downloadable build needs your Apple Developer ID to sign + notarize the DMG
(`macos/scripts/build-dmg.sh` bundles the sidecar; you run
`sign-and-notarize.sh`). That step can't be done in CI without your credentials.
