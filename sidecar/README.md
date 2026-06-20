# Aether Python Sidecar

The sidecar exposes the Phase 2 Python agent loop over HTTP so the native Swift shell can own UX (HUD, voice, permissions) while reusing tools, routing, and memory unchanged.

## Install

From the repo root (with the main venv active):

```bash
pip install -r requirements.txt
pip install -r requirements-sidecar.txt
```

Export tool schemas (once, or after registry changes):

```bash
python scripts/export_tool_schemas.py
```

## Run

```bash
# From repo root
python -m sidecar.server
```

Default bind: `http://127.0.0.1:8765`

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness probe |
| GET | `/status` | Current run + world snapshot |
| GET | `/tools/schemas` | Tool manifest (`shared/tool_schemas/`) |
| POST | `/run` | Start agent (`{"goal":"..."}`); SSE when `stream: true` |
| POST | `/stop` | Global STOP (mirrors Python `stop_ctl`) |
| POST | `/stt` | Transcribe base64 WAV (`{"audio_base64":"..."}`) |

### Example

```bash
curl -s http://127.0.0.1:8765/health

curl -N -X POST http://127.0.0.1:8765/run \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"goal":"Open Finder","stream":true,"local_only":false}'

curl -X POST http://127.0.0.1:8765/stop
```

## SSE event types

- `run_start` — `{run_id, goal}`
- `hud` — `{goal, step, status, last_action, ...}`
- `say` — `{text}` (Swift TTS should speak this)
- `done` — `{result, world}`
- `error` — `{message}`
- `stopped` — user STOP

## MLX / local ML hooks

Configure `configs/router.yaml` and `config.yaml` as in Phase 2. The sidecar does not duplicate ML logic — it loads the same `Agent` class as `python run.py`.

Optional future flag: `voice.stt: mlx` (not implemented in Phase 3; use Apple Speech in Swift or OpenAI via `/stt`).

## Notes

- Only one agent run at a time.
- Python TTS is disabled in sidecar mode; the Swift app speaks `say` events.
- The CLI (`python run.py`) is unchanged and does not require the sidecar.
