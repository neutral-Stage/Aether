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
- **Wake word:** set `beta.wake_word: true` and pick `beta.wake_word_engine`:
  - `speech` (recommended): Apple's recognizer with on-device recognition only, so audio never leaves the Mac. Say "Hey Aether, open my Downloads"; the request runs once the words stop changing for 1.2 s, or say "Hey Aether" and then the request within 6 s. It pauses while Aether speaks, records or works, and restarts its recognition session every 55 s. Needs the Speech Recognition permission and an on-device English model.
  - `porcupine`: Picovoice keyword spotting (needs an access key).
  - `energy`: a loudness placeholder with many false wakes; for testing only.
- **Talk mode latency:** answers stream and are spoken clause by clause. If nothing has been said 0.9 s after you release ⌃⌥, a short filler plays. `first_audio_ms` (release to first sound) is on `/dashboard`.
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

When `voice.mode: realtime`, push-to-talk (hold ⌃Space) goes to the Realtime model instead of the transcribe → run → speak pipeline:

- The microphone streams only while you hold the keys; releasing ends your turn. Nothing is uploaded otherwise.
- The reply plays as it streams (24 kHz). Pressing the keys while it speaks stops it, and the conversation is cut at what you actually heard.
- The model has two tools: `look_at_screen`, which attaches a screenshot as an image before the tool's answer, and `do_task`, which hands a request to Aether's agent. Tasks go through the same policy gate, confirmations and audit log as any run; confirmation panels appear as usual.
- `voice.realtime_model` (default `gpt-realtime`) and `voice.realtime_speaker` (default `marin`) choose the model and its voice.

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
| Realtime beta | Requires `beta.realtime_voice: true` and an OpenAI key; not yet tried on a real Mac | E5 |
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

## Dictation and rewriting

- **⌃⌥D** starts dictating into the focused text field; press it again to finish. The words are transcribed with your vocabulary, cleaned up (punctuation, filler words, spoken "new line" / "comma"), matched to the app's tone (email prose in Mail, short chat in Slack), and pasted. The clipboard is put back afterwards unless something else changed it.
- **⌃⌥T** rewrites the selected text: pick a quick action (fix, shorter, formal, friendlier, bullet points, translate) or type one, check the result next to the original, then Replace. It is pasted, so the app's own Undo reverts it. Secrets in the selection are redacted before it goes to the model.
- Neither works in password fields.
- `GET/PUT /dictation/settings` holds your vocabulary (also sent to speech-to-text as a spelling hint), per-app tones by bundle id, and whether to clean up with the model at all. Settings live in `<data dir>/dictation.json`.
