# Benchmark harness (Phase 6 + Phase 11)

Automated task scoring for Aether agent quality. CI runs **mock mode** (no LLM, no macOS effectors).

## Commands

```bash
# Mock suite (CI-safe) — scores scripted tool traces
python scripts/benchmark_tasks.py --mock

# Repeat / skill-assisted metrics (Phase 11)
python scripts/benchmark_tasks.py --mock --repeat

# Live sidecar (requires API keys + running sidecar)
python scripts/benchmark_tasks.py --sidecar http://127.0.0.1:8765 --token "$AETHER_SIDECAR_TOKEN"
```

## Baseline (June 2026)

| Metric | Value | Notes |
|--------|-------|-------|
| Mock pass rate | **100%** (10/10) | `tests/benchmark/tasks.yaml` scripted traces |
| Repeat pass rate | **100%** | Same traces on simulated repeat runs |
| Skill trace pass rate | **100%** | `mock_skill_trace` when present, else falls back to `mock_trace` |
| Live sidecar pass rate | *not measured in CI* | Run manually with `--sidecar` |

Phase 11 exit criterion: multi-step task success **≥ 60%** on live benchmark — requires manual/nightly runs against a real sidecar (see `.github/workflows/benchmark-nightly.yml`; live mode needs `AETHER_SIDECAR_TOKEN` + LLM API key secrets).

## Memory / skill improvement

The scorer compares:

- `mock_trace` — first-run baseline
- `mock_repeat_trace` — optional shorter repeat path
- `mock_skill_trace` — optional skill-replay path (fewer steps)

`memory_boost` is true when the skill trace passes with fewer or equal tool steps vs baseline.

## Tests

```bash
pytest tests/benchmark/ -q
```
