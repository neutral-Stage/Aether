# Validation log

Results of running Aether against a real Mac, following
[`FIRST_RUN.md`](FIRST_RUN.md). Mock-only test runs do not belong here. Newest first.
Paste the table that `scripts/live_smoke.py --tasks` saves, plus the machine
details. For the unattended benchmark in throwaway VMs
(`scripts/benchmark_tasks.py --vm`, [BENCHMARK_VM.md](BENCHMARK_VM.md)), paste
its pass rate against the 60% bar and the failing task ids.

Template:

```
## YYYY-MM-DD — <Mac model>, macOS <version>, <brain model>

- Build: `make app` at <commit>, identity: Aether Dev | ad-hoc
- Doctor: <verdict>, notable warnings: …
- Grounding calibration: <coord_space> @ <edge> px, <hit rate>, median <n> pt

| Check | Result | Detail |
|---|---|---|
| … | … | … |

Notes: what failed, why, what was changed.
```

## Log

## 2026-11-10 — Apple M5 Max (`arm64`), macOS 27.2 (Build 26B123), `ollama_local` (`qwen3.8:27b-mlx`)

- Build: Python 3.11.18 `.venv` (`requirements.lock`) + `sidecar.server` at `main`, identity: `Aether Dev` (self-signed via `scripts/create-dev-identity.sh`)
- Doctor: `0` failures (`[✓] Python 3.11.18`, `[✓] PyObjC bridges`, `[✓] Vision OCR`, `[✓] Default brain reachable — ollama_local is serving qwen3.8:27b-mlx`, `[✓] Accessibility permission — granted`, `[✓] Screen Recording permission — granted`, `[✓] Vision grounding calibrated`)
- Grounding calibration: `pixels` @ `1280` px, `80%` hits (`4/5`), median `6.7` pt (`qwen3.8:27b-mlx`)
- Result: **13 passed, 0 failed** (`scripts/live_smoke.py --tasks`)

### Keyless + LLM checks (`live_smoke.py --with-llm`)

| Check | Result | Detail |
|---|---|---|
| `GET /health` 200 + ok | pass | `service=aether-sidecar` |
| `GET /catalog` lists apps+tools | pass | `79 tools, 33 apps` |
| `GET /runs` (reconcile ran, no phantom `running` on boot) | pass | `active=0` |
| `GET /events` opens an SSE stream | pass | SSE stream opened |
| Default brain accepts the API key | pass | `ollama_local is serving qwen3.8:27b-mlx` |
| A real goal completes (perceive → reason → finish) | pass | `status=idle result='Ghostty'` |
| Token cost is being tracked | pass | `total_cost_usd=0.0, tokens=19256` |

### Canonical real-Mac tasks (`live_smoke.py --tasks`)

| Check | Result | Detail |
|---|---|---|
| Finder: open Downloads | pass | 5s, status=idle, saw `/Users/sar333/Downloads/` |
| Safari: read a page title | pass | 7s, status=idle, saw `o https://example.com. The page's title is "Example Domain".` |
| Notes: create a note | pass | 219s, status=idle, saw `Aether test, Aether test` |
| Mail: draft (not send) an email | pass | 43s, status=idle, saw `Aether test` |
| Terminal: run a command | pass | 15s, status=idle, saw `env) \n❯ echo aether-ok\naether-ok\n~ via 🐍 v3.13.18 (.venv) \n❯` |
| STOP halts a running task < 2 s | pass | 37 ms, status=stopped |

Notes — real-Mac bugs uncovered and fixed during this validation run:
1. **Headless `NSWorkspace` caching (`aether/perception/accessibility.py`, `aether/effectors/apps.py`)**: `NSWorkspace.sharedWorkspace().frontmostApplication()` and `runningApplications()` remain frozen at startup in a headless Python `uvicorn` process where the main-thread `CFRunLoop` is not pumped. Replaced with live WindowServer queries via `HIServices` (`GetFrontProcess`, `GetNextProcess`, `GetProcessPID`) + `NSRunningApplication.runningApplicationWithProcessIdentifier_`.
2. **Vision OCR `VNImageRequestHandler.extent` (`aether/perception/ocr.py`)**: Guarded `hasattr(handler, "extent")` inside `try:` before falling back to `CGImageSourceCopyPropertiesAtIndex` and `screen.png_dimensions`.
3. **Newline and tab synthesis in `type_text` (`aether/effectors/input.py`)**: `CGEventCreateKeyboardEvent(None, 0, True)` with `"\n"` sent keycode `0` (`kVK_ANSI_A`) instead of Return. Updated `type_text` to dispatch `press_key("return")` on `\n` and `press_key("tab")` on `\t`.
4. **User goal self-taint via `WorldModel.context_block()` (`aether/core/orchestrator.py`)**: Excluded the trusted `Goal: ...` line when scanning `context_block()` for background prompt-injection taint so goals like `"Open Terminal and run the command: echo aether-ok"` do not self-taint the run.
5. **Terminal `AXTextArea` tail visibility (`aether/perception/accessibility.py`)**: Updated `Element.describe()` to preserve the tail (last 180 chars) of `AXTextArea` / `AXTextView` values rather than only the first 60 chars (`"Last login: ..."`).
6. **Sub-50ms `STOP` propagation (`aether/core/orchestrator.py`, `aether/core/llm.py`, `sidecar/server.py`)**: Added `_await_or_stop` polling `abort_event` every 25 ms during LLM calls, checked `abort_event` on every SSE chunk, and preserved `state.status = "stopped"` in `sidecar/server.py` when `agent.world.status == "stopped"`.
