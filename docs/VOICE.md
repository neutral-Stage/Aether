# Voice pipeline — Phase 7 + Phase 10

## Current state

- **Barge-in:** `VoicePipeline.swift` ducks TTS (~85% volume) and stops playback when mic energy or partial STT exceeds threshold.
- **Mic gate (AEC substitute):** `AudioEngine.setMicGated(true)` suppresses energy callbacks during TTS unless energy exceeds `threshold × 2.5` — reduces false barge-in from speaker bleed. **Not true AEC.**
- **VAD:** RMS energy gate in `AudioEngine.startContinuousMonitoring`.
- **STT path:** Apple Speech for partials; Groq Whisper via sidecar `POST /stt` for PTT utterances.
- **TTS path:** Groq Orpheus via sidecar `POST /tts` or streaming `POST /tts/stream`; macOS `AVSpeech` fallback.
- **Streaming TTS (Phase 10):** Swift `TTSBridge` tries `/tts/stream` first, falls back to `/tts` on failure.
- **Realtime voice (Phase 10 beta):** OpenAI Realtime API via `aether/voice/realtime.py` and sidecar `WS /voice/realtime`; Swift `RealtimeVoiceSession.swift`.
- **Metrics:** Swift reports `stt_ms`, `tts_ms`, `voice_rtt_ms` via `POST /metrics/voice`.
- **Wake word:** Energy-based placeholder in `WakeWordDetector.swift` + transcript phrase match (`hey aether`). Porcupine hook documented for `beta.wake_word_engine: porcupine`.
- **Ambient mode:** `AmbientListeningController` when `beta.ambient_listening: true` — HUD shows ear indicator.

## Enabling Realtime voice (beta)

```yaml
# config.yaml
beta:
  realtime_voice: true

voice:
  mode: realtime
  realtime_provider: openai
```

Set `OPENAI_API_KEY` in `.env`. Swift connects to `ws://127.0.0.1:8765/voice/realtime` when both flags are set.

When `voice.mode: realtime`, the STT→LLM→TTS pipeline is bypassed in favor of the WebSocket session (product path still evolving).

## Enabling streaming TTS

```yaml
voice:
  tts: groq
  tts_stream: true   # default true — Swift prefers POST /tts/stream
```

Requires `GROQ_API_KEY` for Groq Orpheus synthesis.

## Known limitations

| Issue | Impact | Phase |
|-------|--------|-------|
| No hardware AEC | Mic gate + duck only; speakers near mic may still false-trigger | 7 (mitigated) / 10 |
| Cloud STT round-trip | Multi-second voice latency vs NFR-1 (800 ms) | 10 |
| Streaming TTS | Chunks fetched over HTTP; playback starts after full WAV assembled (MVP) | 11 |
| Realtime beta | Requires `beta.realtime_voice: true`; no Swift mic uplink yet | 10 spike |
| Energy wake word | High false-positive rate vs Porcupine | 7 stub → Porcupine prod |

## True AEC path (future)

1. `AVAudioEngine` voice-processing I/O unit or `setVoiceProcessingEnabled(true)` where supported.
2. WebRTC `AudioProcessing` software AEC fallback.
3. Disable partial STT during TTS until duck + gate confirms user speech.

## Regression tests

- XCTest: `Tests/AetherTests/AetherSmokeTests.swift` (requires library target split to run via `swift test`; `swift build` validates compile).
- Integration: `POST /metrics/voice`, `GET /config/mcp`, `POST /tts/stream`.
- Unit: `tests/unit/test_mcp_sse.py`, `tests/unit/test_router_failover.py`.
- Manual: PTT → STT → `/run` → TTS with barge-in at 70% volume (`docs/TESTING.md`).

## Metrics (VOICE-004)

`voice_roundtrip_ms` histogram in sidecar metrics when STT + agent + TTS path is instrumented via `POST /metrics/voice`.
