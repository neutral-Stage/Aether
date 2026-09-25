# First real run (the Phase A gate)

Aether's tests run on mocks. This is the checklist that proves it works on a real
Mac. Do the steps in order; each has a check that tells you what is wrong. Record
the result in [`VALIDATION_LOG.md`](VALIDATION_LOG.md). Nothing past Phase A is
trusted until this passes.

You need macOS 14+ (Apple Silicon recommended), Xcode command-line tools, Python
3.11, `uv` (`brew install uv`), and a Z.ai API key (the default brain is
GLM-5.3-Flash). Groq (voice) and Anthropic (failover) keys are optional.

## 1. Install and preflight

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock          # exact pins; or requirements.txt + requirements-sidecar.txt
make ci                                   # lint, tests, packs, mock benchmark, Swift build + tests
make doctor                               # add --online to test the API key: python -m aether.app --doctor --online
```

Every `✗` in the doctor output prints its fix. A `!` is a degraded but usable warning.

## 2. Build the app

```bash
make dev-identity     # once: a self-signed "Aether Dev" code-signing identity
make app              # builds macos/build/Aether.app with its own Python sidecar
open macos/build/Aether.app
```

Why the identity: macOS ties Accessibility and Screen Recording grants to the app's
signature. An ad-hoc signature changes on every build, so the grants would reset each
time you rebuild. The script falls back to ad-hoc (with a warning) if the identity is
missing.

## 3. Onboarding

The app opens its onboarding sheet on first launch:

1. **Permissions.** Grant Accessibility, Screen Recording, Microphone, Speech
   Recognition and Input Monitoring. Screen Recording and Input Monitoring apply
   after you quit and reopen Aether.
2. **API key.** Paste the Z.ai key (Groq and Anthropic optional). Keys go to the
   Keychain; the sidecar restarts with them.
3. **Check setup.** "Run checks" shows the same report as `make doctor`; "Test API
   key" confirms the provider accepts the key.

## 4. Calibrate vision grounding

Bring a window full of labelled controls to the front (System Settings is ideal):

```bash
python scripts/calibrate_grounding.py --write
```

This measures how accurately the vision model points at known UI elements, picks
the coordinate convention and image size that work best on this Mac, and saves them.
Under 60% hits means pointing will lean on the accessibility tree and the
crop-refine pass.

## 5. Smoke the live stack

```bash
python scripts/live_smoke.py              # sidecar up, catalog, runs, event stream
python scripts/live_smoke.py --with-llm   # API key + one real goal + cost tracking
python scripts/live_smoke.py --tasks      # the five canonical tasks + STOP latency
```

With the app running, the sidecar is already up and has the token and keys. For a
terminal-only run, start it yourself with `python -m sidecar.server` and export the
keys.

`--tasks` drives real apps and then reads the outcome back with AppleScript:

| Task | Verified by |
|---|---|
| Finder: open Downloads | front Finder window's path ends in `/Downloads` |
| Safari: read a page title | the run's answer contains "Example Domain" |
| Notes: create a note | a note named "Aether test" exists |
| Mail: draft (not send) an email | an outgoing message with subject "Aether test" exists |
| Terminal: run a command | the front Terminal tab contains `aether-ok` |
| STOP | a long task stops within 2 s of `POST /stop` |

It saves a result table under `~/Library/Application Support/Aether/validation/`
(or `data/validation/` in a dev checkout). Paste it into
[`VALIDATION_LOG.md`](VALIDATION_LOG.md). Clean up the test note and draft afterwards.

## 6. From the app

- Press ⌥Space, type "open Safari and go to apple.com", and watch the HUD.
- Hold ⌃Space and say the same thing.
- Mid-task, press ⌃⇧S (or the HUD's STOP). It should halt immediately.
- `curl -s http://127.0.0.1:8765/audit/verify` (with the bearer token if set)
  should report `ok: true`.
- Hold ⌃Space, say something, and release. The macOS menu-bar mic indicator (the
  orange dot) should turn off within a moment of releasing — if it stays on,
  something is still holding the microphone open.
- ⌃⌥D into a text field and dictate a full sentence. The pasted transcript should
  read back correctly, not garbled, sped up or slowed down — that would mean the
  recording isn't actually at 16 kHz.

## Coding agents (optional)

Put `claude` or `codex` on PATH for `spawn_agent`, `spawn_graph` and
`delegate_to_coder`, then `python scripts/live_smoke.py --with-agents`. It spawns a
real session and checks that STOP kills it within 3 s.

## Distribution

A build other people can download needs a paid Apple Developer ID to sign and
notarize (`macos/scripts/sign-and-notarize.sh`, see `macos/SIGNING.md`). A personal
build does not.
