# Aether — macOS Native Shell (Phase 5)

Phase 5 adds **global PTT** (`⌃Space` hold), **update checking**, crash-safe
STOP, and signing docs. Phase 4 voice, MCP, skills, and observability remain.

## Layout

```
macos/
├── Aether/
│   ├── Sources/Aether/
│   │   ├── Core/           # OrchestratorClient, WorldModel, STOP
│   │   ├── HUD/            # NSPanel overlay + live transcript
│   │   ├── Voice/          # AudioEngine, STTBridge, TTSBridge, VoicePipeline
│   │   ├── Perception/     # AX reader + ScreenCaptureKit frame capture
│   │   ├── Effectors/      # CGEvent + AXPress proof path
│   │   └── Onboarding/     # TCC permission flow
│   └── ...
└── run-dev.sh
```

## Quick start (dev)

```bash
source .venv/bin/activate
pip install -r requirements.txt -r requirements-sidecar.txt
./macos/run-dev.sh
```

Verify:

```bash
curl -s http://127.0.0.1:8765/health
curl -s http://127.0.0.1:8765/metrics
open http://127.0.0.1:8765/dashboard
```

## Phase 4 voice (barge-in)

`VoicePipeline` coordinates:

1. **TTS** via `AVSpeechSynthesizer` while mic tap stays active.
2. **VAD-lite** — RMS energy threshold ducks TTS volume.
3. **Barge-in** — energy spike or partial STT → immediate `stopSpeaking`.
4. **HUD** shows partial transcript during interruption.

Toggle in main window: **Barge-in (interrupt speech)**.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Swift Aether.app                                       │
│  VoicePipeline · Barge-in · HUD transcript              │
│  ScreenCaptureKit (single frame)                        │
└───────────────────────────┬─────────────────────────────┘
                            │ HTTP/SSE :8765
                            ▼
┌─────────────────────────────────────────────────────────┐
│  Python sidecar                                         │
│  Agent · MCP tools · Skills · Metrics · /dashboard      │
└─────────────────────────────────────────────────────────┘
```

## Build

```bash
cd macos/Aether
swift build -c release
.build/release/Aether
```

## Phase 5 features (native)

| Feature | Status |
|---------|--------|
| Barge-in during TTS | ✅ Energy VAD + partial STT |
| Global PTT hotkey | ✅ Hold `⌃Space` (Input Monitoring required) |
| Global STOP | ✅ `⌃⇧S` + sidecar `/stop` |
| Update checker | ✅ GitHub releases JSON (configure URL) |
| ScreenCaptureKit | ✅ Single-frame PNG capture |
| Continuous stream | ❌ Beta flag off by default |
| Code sign / notarize | 📄 See `SIGNING.md` |

## Tests

```bash
python -m compileall -q aether sidecar tests
python -m pytest tests/security/ -q
cd macos/Aether && swift build
```

See **`BETA.md`** and **`macos/SIGNING.md`** for beta install and distribution.
