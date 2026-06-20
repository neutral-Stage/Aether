# Native effectors (Phase 8)

Python tools (`click`, `type_text`) run via pyobjc/CGEvent by default. When
`beta.native_effectors: true` in `config.yaml`, the registry tries the Swift HTTP
bridge first.

## Architecture

```
Sidecar (Python registry) ──POST /invoke──▶ Swift NativeEffectorServer :8766
                                              └── InputController (CGEvent)
```

## Enable

```yaml
beta:
  native_effectors: true
```

Start the macOS app (it listens on `127.0.0.1:8766` by default). Verify:

```bash
curl -s http://127.0.0.1:8766/health
```

## Hybrid path (default)

Keep `native_effectors: false` — all actions stay in Python. Use Swift effectors
only for latency experiments or when Python pyobjc is unavailable in a given
environment.

## Future

- AX press via `AXActions.swift` on the hot path
- Unix socket + typed IPC schemas in `shared/ipc/`
- Sidecar-initiated push instead of HTTP polling
