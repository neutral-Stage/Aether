# Aether Phase 1 — Vision Fallback + Model Router + Broader App Coverage

**Date:** 2026-06-19 · **Status:** Approved design (pre-implementation) · **Repo state at design time:** `1.0.0-rc.1`

This spec turns the "Phase 1" bundle (vision fallback + model router + broader app coverage) into a concrete, file-level design. It was written after a full line-level audit of the relevant modules (see [Current State](#current-state)). Scope was confirmed as **all outstanding items** in the three areas, built **vertical-by-area** (router → vision → packs).

---

## Goals

1. **Model router** — make LLM spend observable and make the local fast loop reliable.
   - Capture token usage from every backend and compute estimated cost.
   - Surface per-provider tokens + cost on `/metrics` and `/dashboard`.
   - Replace the local model's fragile JSON-in-text tool calling with native Ollama tool-calling, feature-gated with a fallback.
2. **Vision fallback** — make the vision tier content-aware and crash-resistant.
   - One shared screen-content classifier (text-heavy vs sparse/graphical).
   - Auto OCR-only on text-heavy screens (skip the cloud VLM); keep the VLM for graphical/sparse screens.
   - Detect "AX present but wrong" and route to vision anyway.
   - Degrade gracefully when screen capture is denied/fails instead of crashing the tier.
3. **Broader app coverage** — close the knowledge-pack cold-start bug and add 16 new packs covering the stock Apple stack plus key third-party apps.

## Non-Goals

- No billing-accurate cost (token-count estimates only; Anthropic cache-tier pricing and failover-retry tokens are explicitly out of scope, only noted).
- No rewrite of the orchestrator, router, or LLM abstraction; only additive changes.
- No streaming-token accounting (current pipeline is non-streaming for the agent loop).
- No new VLM provider; vision routing only chooses **between OCR-only and the already-configured VLM**.
- No pack marketplace UI (sideload already exists; out of scope here).

---

## Current State

Audited modules and the concrete gaps found:

| Area | Finding |
|------|---------|
| `LLMResponse` ([llm.py:43](../../../aether/core/llm.py)) | No token/usage fields; all 4 backends discard `resp.usage`. |
| `MetricsCollector` ([metrics.py](../../../aether/core/metrics.py)) | No token/cost dimension; `record_step` takes only `(route_tier, latency)`. No price table anywhere in repo. |
| `LocalHTTPClient` ([llm.py:349](../../../aether/core/llm.py)) | Tools sent as truncated plaintext names (`_format_tools_for_local`), tool calls parsed via regex (`_parse_tool_calls_from_text`). No native Ollama `tools` field, no `message.tool_calls` parsing. |
| Vision routing ([router.py:127](../../../aether/core/router.py)) | VISION chosen purely from AX `element_count`; `ocr_only` is a **static** `router.yaml` flag. No screen-content signal. |
| `vision.analyze_screen` / `VisionLLM.analyze_screenshot` | Two parallel OCR entry points; neither classifies content; OCR-vs-VLM is a caller flag, not content-driven. |
| `screen.capture_to_file` ([screen.py:16](../../../aether/perception/screen.py)) | Raises on permission-denied/timeout; called unguarded → crashes vision tier. |
| Knowledge packs ([loader.py](../../../aether/knowledge/loader.py)) | **Cold-start bug:** a pack's embedded `bundle_ids`/`aliases` register only *after* load, but a brand-new app can't be resolved to load → pack never fires unless static maps are hand-edited. |
| Config/schema | `config.schema.json` has `additionalProperties: true` everywhere (new flags work without schema edits); `router.yaml` is **not** schema-validated. |
| Tests | `tests/unit/test_router.py` is pure logic over the `world` fixture; `test_router_failover.py` shows the `_FakeClient` + `@patch(create_client)` styles. No `test_metrics.py` or `test_vision.py`. `MetricsCollector` singleton has **no** test-reset fixture. |

Existing 17 packs: `calendar, chrome, davinci_resolve, figma, finder, logic_pro, mail, notes, notion, office, safari, slack, spotify, terminal, vscode, xcode, zoom`.

---

## Milestone A — Model Router

### A1. Token capture (shared carrier)

- Add **optional** fields to `LLMResponse` ([llm.py:43](../../../aether/core/llm.py)): `input_tokens: int | None = None`, `output_tokens: int | None = None`, `cost_usd: float | None = None`. Optional + defaulted so all existing constructors and tests keep working and `FailoverLLMClient` passthrough is unaffected.
- Populate at each backend's `step()` construction site, each **defensively guarded** (`resp.usage` may be `None` on some OpenAI-compatible proxies — accounting is skipped, the turn never crashes):
  - Anthropic ([llm.py:116](../../../aether/core/llm.py)) → `resp.usage.input_tokens` / `resp.usage.output_tokens`.
  - OpenAI-compatible ([llm.py:259](../../../aether/core/llm.py), also covers `GoogleGeminiClient` via inheritance) → `resp.usage.prompt_tokens` / `resp.usage.completion_tokens`.
  - Local/Ollama ([llm.py:411](../../../aether/core/llm.py)) → top-level `prompt_eval_count` / `eval_count`; OpenAI-style local server → `body['usage']`.
  - `VisionLLM.step` delegates → usage propagates from the wrapped client (no change needed); its `analyze_screenshot` VLM HTTP path is accounted in A3.

### A2. Cost accounting

- Module-level `PRICE_TABLE` in [metrics.py](../../../aether/core/metrics.py) (next to `LATENCY_BUDGETS_MS`), keyed by **`(provider, model)`** with a **per-provider fallback** and a **`local_http` → $0** default. Prices stored as USD per 1K input / output tokens. Unknown provider → $0 **plus** an `unknown_provider_usage` counter (never raises `KeyError`).
  - The provider key is the string in `LLMResponse.backend` (`anthropic`, `google`, `local_http`, or the `provider_label` such as `zai`, `openrouter`, `groq`, `kilo`, `kie`, `fireworks`, `zai_vision`). Verified via `providers.create_client`.
  - Pricing lives in code (a constant) for a single observability surface; values are clearly marked **estimates**.
- `RunMetrics` ([metrics.py:23](../../../aether/core/metrics.py)) gains `tokens_in: int = 0`, `tokens_out: int = 0`, `cost_usd: float = 0.0`.
- New `MetricsCollector.record_llm_usage(provider, model, tokens_in, tokens_out)`: under `self._mutex`, `inc()` flat counters `tokens_in_{provider}` / `tokens_out_{provider}` (auto-appear in the existing dashboard Counters table), compute cost via `PRICE_TABLE`, accumulate into a new `self._provider_costs` dict (`{provider: {tokens_in, tokens_out, cost_usd, calls}}`) and into `self._current_run`. Cheap, no I/O, no nested lock acquisition (mirrors `record_step`).
- `snapshot()` ([metrics.py:124](../../../aether/core/metrics.py)) gains `provider_costs` and a top-level `total_cost_usd`. Long-lived cumulative totals live in `_provider_costs`/counters (survive the `_max_runs=50` eviction).

### A3. Wire every LLM call site (no undercounting)

The single biggest accuracy risk: there are **four** LLM call paths, not one. `_reason_step` will be changed to return usage (extend the returned tuple / attach to `resp`) and the orchestrator records it once per call at each site:

1. Main step ([orchestrator.py:227](../../../aether/core/orchestrator.py)) — record next to `record_step` at L363.
2. Local→cloud fallback second `step()` (L245-251) — second call, must be recorded.
3. Self-correction turn (L523).
4. Vision-tier `analyze_screenshot` VLM call inside `_maybe_vision_context` (L178-187) — its tokens are outside the main `client.step` path.

### A4. Dashboard + metrics surface

- `GET /metrics` ([server.py:521](../../../sidecar/server.py)) — **no change**; JSON passthrough of `snapshot()` auto-includes `provider_costs` + `total_cost_usd`.
- `GET /dashboard` ([server.py:526](../../../sidecar/server.py)) — add a **"Cost & tokens (estimated)"** `<h2>` + `<table>` built from `snap.get('provider_costs', {})` (provider, tokens in/out, calls, cost USD) with a grand-total row, injected into the existing f-string. Label explicitly as estimates.

### A5. Native local tool-calling

- New `_format_tools_for_local_native(tools)` mapping Anthropic tool schema → Ollama `[{type: 'function', function: {name, description, parameters: <input_schema>}}]` (mirror `_anthropic_tools_to_openai` at [llm.py:559](../../../aether/core/llm.py)), passing **full `input_schema`** (current plaintext path sends names only).
- In `LocalHTTPClient.step`: when native tools are enabled, add `payload['tools']` and **skip** the JSON-in-text hint; parse `body['message'].get('tool_calls')` first, building `{id: local_<uuid>, name, input}` (reuse `_json_to_tool_call`'s id scheme so tool-result echo-back stays consistent). Fall back to `_parse_tool_calls_from_text(text)` when no native `tool_calls` are present.
- **Feature-gated** by a new `local_fast.native_tools: true` flag in `router.yaml` (read by `LocalHTTPClient`/router). Default chosen at implementation: **off** to preserve current behavior unless explicitly enabled, since non-Ollama local servers (MLX, llama.cpp without an OpenAI shim) and older models reject the `tools` field. The JSON-in-text path is always retained as fallback.

### A Acceptance criteria

- [ ] Every backend populates token fields when the provider returns usage; `resp.usage is None` does not raise.
- [ ] `record_llm_usage` computes correct cost for a known `(provider, model)`, $0 for `local_http`, and $0 + counter for unknown providers.
- [ ] All four LLM call sites record usage (verified by a test that exercises the local→cloud fallback and asserts two usage records).
- [ ] `snapshot()['provider_costs']` and `total_cost_usd` present; `/dashboard` renders the cost table.
- [ ] With `native_tools: true`, a mocked Ollama response with `message.tool_calls` is parsed natively; with the flag off (or no native calls returned), JSON-in-text parsing still works.

---

## Milestone B — Vision Fallback

### B1. Shared screen-content classifier

- Pure function `classify_screen_content(regions, image_w, image_h, *, min_conf=0.3)` in [ocr.py](../../../aether/perception/ocr.py) returning:
  `{label: 'text_heavy'|'sparse'|'graphical'|'empty', score: float, char_count: int, text_coverage: float, region_count: int, mean_confidence: float}`.
- Filters regions below `min_conf` before aggregating (Apple Vision emits spurious low-confidence boxes on photos/graphics). `text_coverage` = Σ(region area) / image area; needs `image_dimensions()` — if it returns `(0,0)`, label degrades to `'unknown'` (no divide-by-zero, never defaults to `text_heavy`).
- `text_heavy` requires **high char_count AND high coverage AND reasonable mean_confidence** — region count alone is insufficient. Lives in `ocr.py` (pure, no router/LLM import) so both OCR entry points share one implementation.

### B2. Single-OCR helper (perf)

- Add `recognize(image_path) -> (formatted: str, regions: list[TextRegion], dims: (w, h))` so callers needing both the formatted string and raw regions don't run Apple Vision's Accurate pass twice per step. Existing `recognize_text` / `recognize_text_formatted` remain for back-compat.

### B3. Auto OCR-only

- `vision.analyze_screen` ([vision.py:12](../../../aether/perception/vision.py)) and `VisionLLM.analyze_screenshot` ([llm.py:451](../../../aether/core/llm.py)) consult the classifier:
  - `text_heavy` → **skip the cloud VLM** (run OCR-only); record `content_class` on the result.
  - `sparse` / `graphical` → keep the VLM.
  - A coverage/confidence **band** still escalates to the VLM when the screen is text-heavy *and* likely needs layout/chart interpretation (avoids losing information on annotated graphics).
- Precedence rule: the classifier may override the static `router.yaml` `ocr_only` flag **toward** OCR-only, but never forces the VLM when config says `ocr_only: true`.

### B4 + B5. Content-aware routing + "AX present but wrong"

- New `WorldModel` fields ([world_model.py](../../../aether/core/world_model.py)): `screen_content_class: str = 'unknown'`, `text_heavy_score: float = 0.0` (and optional `ocr_region_count: int = 0`), set after OCR in `_maybe_vision_context` so the **next** `route()` call can use them.
- `Router.route()` ([router.py:127](../../../aether/core/router.py)) reads `world.screen_content_class` to choose `ocr_only` dynamically in `_get_or_create_vision_with_failover`.
- **AX-present-but-wrong:** when `element_count >= ax_empty_threshold` (AX looks sufficient, VISION normally skipped) **but** the screen is `text_heavy` while AX exposes little `AXStaticText` coverage (canvas/Electron/doc apps), route VISION anyway. Gated on an actual AX-text-coverage shortfall — **not** on `text_heavy` alone — to avoid over-routing legitimately text-heavy native apps whose AX is fine.

### B6. Capture hardening + tuning

- Guarded capture helper: on `CalledProcessError`/`TimeoutExpired` (e.g. Screen Recording permission denied), return `None`/sentinel and log a **one-time** warning; callers (`world_model.capture_screenshot`, `vision.analyze_screen`) degrade to AX-only context instead of crashing. The warning must remain visible so a real permission problem isn't masked.
- New tunables in `router.yaml` `routing:` block (read via `self.cfg.routing.get(...)`, mirroring `ax_empty_threshold`): `text_heavy_char_threshold`, `text_coverage_threshold`, `min_region_confidence`, `ax_text_coverage_threshold`.

### B Acceptance criteria

- [ ] `classify_screen_content` returns `text_heavy` for a dense-text region set, `graphical` for many low-conf boxes, `unknown` when dims are `(0,0)` — covered by `test_vision.py`.
- [ ] OCR runs once per vision step (no double Accurate pass).
- [ ] On a `text_heavy` screen the cloud VLM is skipped; on `graphical` it is used.
- [ ] `route()` returns VISION for the AX-present-but-wrong case (high OCR text + low AX text coverage) and does **not** for a normal text-heavy native app with good AX coverage.
- [ ] A simulated capture failure degrades to AX-only without raising, and logs once.

---

## Milestone C — Broader App Coverage

### C1. Fix the cold-start gap (do first)

- Add a **pre-warm pass** in [loader.py](../../../aether/knowledge/loader.py) that iterates `list_packs()` and calls `_load_pack_file` for each at startup, so every pack's embedded `bundle_ids`/`aliases` self-register into `_BUNDLE_TO_PACK` / `_APP_ALIASES` before any resolution. This makes new packs work **from their YAML alone** — no per-app `loader.py` edits.
- Keep it cheap: pre-warm is `@lru_cache`-backed and runs once; respects the existing `maxsize` (raise `_load_pack_file`'s cache cap if pack count approaches it — there will be 33 packs, current cap is 32, so **bump the `lru_cache` maxsize** accordingly).
- As belt-and-suspenders for the highest-value apps, still add static `_BUNDLE_TO_PACK`/`_APP_ALIASES` entries where trivial.

### C2. New packs (16)

Modern self-registering template for each: `app`, `tier`, `bundle_ids`, `aliases`, `shortcuts` (≤12, `"KEYS — desc"`), `recipes` (distinct task-aligned tokens so the word-overlap matcher fires reliably), `gotchas` (≤6), and `scripting` for tier 0/1 packs with real Apple Events hooks.

| Pack file | app | tier | bundle id (verify at impl) |
|-----------|-----|------|----------------------------|
| `messages.yaml` | Messages | 1 | `com.apple.MobileSMS` |
| `facetime.yaml` | FaceTime | 2 | `com.apple.FaceTime` |
| `contacts.yaml` | Contacts | 1 | `com.apple.AddressBook` |
| `reminders.yaml` | Reminders | 1 | `com.apple.reminders` |
| `keynote.yaml` | Keynote | 1 | `com.apple.iWork.Keynote` |
| `pages.yaml` | Pages | 1 | `com.apple.iWork.Pages` |
| `numbers.yaml` | Numbers | 1 | `com.apple.iWork.Numbers` |
| `photos.yaml` | Photos | 2 | `com.apple.Photos` |
| `music.yaml` | Music | 1 | `com.apple.Music` |
| `preview.yaml` | Preview | 2 | `com.apple.Preview` |
| `system_settings.yaml` | System Settings | 2 | `com.apple.systempreferences` |
| `maps.yaml` | Maps | 2 | `com.apple.Maps` |
| `arc.yaml` | Arc | 2 | `company.thebrowser.Browser` |
| `discord.yaml` | Discord | 2 | `com.hnc.Discord` |
| `obsidian.yaml` | Obsidian | 2 | `md.obsidian` |
| `linear.yaml` | Linear | 2 | `com.linear.linear` |

> **Implementation note:** every bundle id above must be verified on the build machine with `osascript -e 'id of app "<Name>"'` before committing; `system_settings`/`maps` recipes should prefer URL schemes (`x-apple.systempreferences:`, `maps://`) and `obsidian` the `obsidian://` URI where deterministic.

### C3. Validation + docs

- `make validate-packs` ([scripts/validate_packs.py](../../../scripts/validate_packs.py)) must pass for all new packs (non-empty mapping, `app: str`, `tier: int ∈ 0..3`, correctly-typed optional fields).
- Update the README/ROADMAP pack inventory count (17 → 33).

### C Acceptance criteria

- [ ] Pre-warm registers every pack's `bundle_ids`/`aliases`; a new pack resolves by bundle id **without** editing static maps (covered by a loader test).
- [ ] All 16 new packs pass `make validate-packs`.
- [ ] `_load_pack_file` cache cap accommodates 33 packs.

---

## Cross-Cutting — Testing & Config

- **`tests/unit/test_metrics.py`** (new): cost math for known `(provider, model)`, `local_http` = $0, unknown provider = $0 + counter, per-run accumulation. Add an **autouse `MetricsCollector` reset fixture** to `conftest.py` (mirror `_reset_stop`) — the singleton has no reset today, risking cross-test counter leakage.
- **`tests/unit/test_vision.py`** (new): classifier labels + thresholds; `route()` heuristic tests for content-aware vision and AX-present-but-wrong, using the pure-logic `world`-fixture pattern. Extend the `world` fixture with the new fields defaulted to **non-triggering** values.
- **Native-tools toggle test**: mirror `test_p0_hardening.py`'s monkeypatch-`load_config` pattern.
- **Config home (decision)**: all new knobs (`native_tools` on the `local_fast` role; `text_heavy_char_threshold`, `text_coverage_threshold`, `min_region_confidence`, `ax_text_coverage_threshold` in the `routing:` block) live in **`router.yaml`**, matching the existing `ax_empty_threshold`/`failure_threshold_cloud` pattern. `router.yaml` is **not** covered by `config.schema.json`, so these are **not** schema-validated; instead, the router **guards type/range in code** (clamp thresholds to sane ranges, coerce `native_tools` to `bool`) with defaults applied via `self.cfg.routing.get(..., <default>)`. Consequence: **no `config.schema.json` change and no new `test_config.py` test are required** for Phase 1. `PRICE_TABLE` is a **code constant** in `metrics.py` (not config). This deviates from the "add a schema node" suggestion deliberately, to stay consistent with how router knobs already live.
- **Marker discipline**: every new test carries `@pytest.mark.unit` (`--strict-markers`). **Do not change `Router.__init__` signature** (both router test files depend on it).

---

## Sequencing

Vertical-by-area, each milestone independently shippable and CI-green:

1. **A — Router**: A1 (token capture) → A2 (cost) → A3 (wire all sites) → A4 (dashboard) → A5 (native local tools) → A tests.
2. **B — Vision**: B1 (classifier) → B2 (single-OCR) → B3 (auto OCR-only) → B4/B5 (routing + AX-wrong) → B6 (capture hardening + tunables) → B tests.
3. **C — Packs**: C1 (cold-start fix) → C2 (16 packs) → C3 (validate + docs).

> The classifier (B1) and cost accounting (A2) are the two reusable primitives; building them inside their owning module (`ocr.py`, `metrics.py`) as pure/standalone units keeps the vertical ordering without rework.

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Adding `LLMResponse` fields breaks constructors/tests | Fields optional with defaults; audit all 4 construction sites + tests. |
| `resp.usage is None` on some providers | Guard every populate site; skip accounting, never raise. |
| Provider-only pricing is inaccurate | Key `PRICE_TABLE` by `(provider, model)` with provider fallback; label all figures **estimates**. |
| Undercounting cost on fallback/self-correction/vision calls | Record at all four call sites via `_reason_step` returning usage. |
| Native `tools` rejected by non-Ollama local servers | Feature-gate (`native_tools`, default off) + always retain JSON-in-text fallback. |
| Double OCR doubles Apple Vision cost | `recognize()` returns formatted + regions + dims in one pass. |
| Misclassifying graphical screens as text-heavy | Confidence filtering + coverage requirement; `(0,0)` dims → `unknown`. |
| Over-routing to VISION (latency/cost) | AX-present-but-wrong gated on AX-text-coverage shortfall, not text_heavy alone. |
| Capture-failure masking real permission issues | Degrade to AX-only **and** log a visible one-time warning. |
| Pack cold-start gap returns / cache overflow at 33 packs | Pre-warm pass + bump `_load_pack_file` `lru_cache` maxsize. |
| `MetricsCollector` singleton leaks across tests | Autouse reset fixture in `conftest.py`. |
| `--strict-markers` fails on unmarked tests | Mark every new test `@pytest.mark.unit`. |

---

## File-Change Inventory

**Modify**
- `aether/core/llm.py` — `LLMResponse` fields; populate usage in 3 backends; native-local-tools formatter + `tool_calls` parse; `VisionLLM.analyze_screenshot` classifier hook.
- `aether/core/metrics.py` — `PRICE_TABLE`, `RunMetrics` fields, `record_llm_usage`, `snapshot` keys.
- `aether/core/orchestrator.py` — record usage at all 4 LLM call sites; write content-class to world in `_maybe_vision_context`.
- `aether/core/router.py` — content-aware `ocr_only`; AX-present-but-wrong branch; read new thresholds; `native_tools` plumbing.
- `aether/core/world_model.py` — `screen_content_class`, `text_heavy_score`, `ocr_region_count`.
- `aether/perception/ocr.py` — `classify_screen_content`, `recognize()`.
- `aether/perception/vision.py` — classifier-driven OCR-vs-VLM; `content_class` on result.
- `aether/perception/screen.py` — guarded capture helper.
- `aether/knowledge/loader.py` — pre-warm pass; `lru_cache` maxsize bump; select static map entries.
- `sidecar/server.py` — `/dashboard` cost table.
- `configs/router.yaml` — `local_fast.native_tools` flag + vision text-heavy thresholds in the `routing:` block (no cost table — pricing is a code constant).
- `tests/conftest.py` — metrics reset fixture; `world` fixture new fields.
- `README.md` / `docs/ROADMAP.md` — pack inventory count.

> No `configs/config.schema.json` change: all new knobs live in the (unvalidated) `router.yaml`, range-guarded in code — see Cross-Cutting.

**Create**
- `aether/knowledge/packs/{messages,facetime,contacts,reminders,keynote,pages,numbers,photos,music,preview,system_settings,maps,arc,discord,obsidian,linear}.yaml` (16).
- `tests/unit/test_metrics.py`, `tests/unit/test_vision.py`.
