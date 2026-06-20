# Aether Phase 1 — Vision Fallback + Model Router + Broader App Coverage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Aether's model router observable (token + cost tracking) and its local loop reliable (native tool-calling), make the vision tier content-aware and crash-resistant, and expand knowledge-pack app coverage from 17 to 33.

**Architecture:** Additive changes to the existing Python sidecar. Token usage flows through a widened `LLMResponse` into a new `MetricsCollector` cost path; a pure screen-content classifier in `ocr.py` is shared by the vision execution path and the router; new knowledge packs self-register via a loader pre-warm pass.

**Tech Stack:** Python 3.10, asyncio, FastAPI (sidecar), pytest (`--strict-markers`), Apple Vision (pyobjc, optional), Ollama HTTP, YAML knowledge packs.

## Global Constraints

- Python target: **3.10**; use `from __future__ import annotations`; type hints with `X | None` style (matches existing code).
- Tests: every test class/function carries **`@pytest.mark.unit`** (pytest.ini uses `--strict-markers`); place under `tests/unit/` unless integration.
- **Do NOT change `Router.__init__` signature** — both `tests/unit/test_router.py` and `tests/unit/test_router_failover.py` depend on it.
- New `LLMResponse` fields MUST be **optional with defaults** (5 construction sites + tests rely on the current shape).
- Every provider-usage read MUST be **guarded** (`resp.usage` may be `None`) — accounting is skipped, the LLM turn never raises.
- New router knobs live in **`configs/router.yaml`** (not `config.yaml`/schema), read via `self.cfg.routing.get(name, default)`; `router.yaml` is **not** schema-validated, so guard ranges in code.
- `PRICE_TABLE` is a **code constant** in `metrics.py`; all cost figures are **estimates** and must be labeled as such in the dashboard.
- Cost classifier and pricing are **pure functions** (no I/O, no singletons) so they unit-test trivially.
- **Git note:** this working copy is **not a git repository**. Either run `git init` once before starting, or treat each "Commit" step as a checkpoint and skip the `git` command. Commands below assume git exists.
- Verification commands run from repo root: `python -m compileall aether sidecar tests`, `ruff check aether sidecar tests`, `python -m pytest tests/ -q`, `make validate-packs`.

---

## File Structure

**Modify**
- `aether/core/llm.py` — `LLMResponse` usage fields; per-backend usage population; native-Ollama tools path.
- `aether/core/metrics.py` — `PRICE_TABLE`, `estimate_cost_usd`, `record_llm_usage`, `RunMetrics` fields, `snapshot` keys.
- `aether/core/orchestrator.py` — record usage inside `_reason_step`; content-class write-back + capture-None guard in `_maybe_vision_context`.
- `aether/core/router.py` — `native_tools` plumbing for local; AX-present-but-wrong branch reading new thresholds.
- `aether/core/world_model.py` — `screen_content_class`, `text_heavy_score`, `ax_text_ratio`; guarded `capture_screenshot`.
- `aether/perception/ocr.py` — `classify_screen_content`, `recognize`, `_format_regions` refactor.
- `aether/perception/vision.py` — classifier-driven OCR-vs-VLM; `content_class` on result.
- `aether/perception/screen.py` — `try_capture_to_file` guarded helper.
- `aether/knowledge/loader.py` — pre-warm pass; `lru_cache` maxsize bump.
- `sidecar/server.py` — `/dashboard` cost table.
- `configs/router.yaml` — `local_fast.native_tools`; vision thresholds in `routing:`.
- `tests/conftest.py` — metrics reset fixture; `world` fixture new fields.
- `README.md` / `docs/ROADMAP.md` — pack inventory count (17 → 33).

**Create**
- `tests/unit/test_llm_usage.py`, `tests/unit/test_metrics.py`, `tests/unit/test_local_native_tools.py`, `tests/unit/test_vision.py`, `tests/unit/test_screen_capture.py`, `tests/unit/test_loader_prewarm.py`.
- `tests/integration/test_dashboard_cost.py`.
- `aether/knowledge/packs/{messages,facetime,contacts,reminders,keynote,pages,numbers,photos,music,preview,system_settings,maps,arc,discord,obsidian,linear}.yaml` (16).

---

# Milestone A — Model Router

## Task A0: Fix re-entrant metrics deadlock (prerequisite)

**Why:** `MetricsCollector.record_step` / `record_tool` / `start_run` / `end_run` call `self.inc()` / `self.observe()` **while holding** `self._mutex`, which is a **non-reentrant `threading.Lock`**. This deadlocks on the first metrics write of any live run (`start_run` runs first). Verified: `MetricsCollector().record_step('local_fast', 1.0)` hangs. Phase 1 cost tracking (Task A3) records in the same loop, so this MUST be fixed first. One-line fix: use `RLock`.

**Files:**
- Modify: `aether/core/metrics.py` (`MetricsCollector.__init__` :44)
- Test: `tests/unit/test_metrics_locking.py` (create)

**Interfaces:**
- Produces: `MetricsCollector` methods that compose (`record_step`, `record_tool`, `start_run`, `end_run`) no longer deadlock.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_metrics_locking.py`:

```python
"""Regression: composed MetricsCollector methods must not deadlock (Phase 1)."""
from __future__ import annotations

import pytest

from aether.core.metrics import MetricsCollector


@pytest.mark.unit
class TestMetricsNoDeadlock:
    def test_record_step_completes(self) -> None:
        m = MetricsCollector()
        m.record_step("local_fast", 12.3)  # hangs today (re-entrant Lock)
        assert m.snapshot()["counters"]["steps_total"] == 1

    def test_run_lifecycle_completes(self) -> None:
        m = MetricsCollector()
        m.start_run("r1", "goal")
        m.record_step("cloud_frontier", 5.0)
        m.record_tool("click", 3.0)
        m.end_run("completed")
        snap = m.snapshot()
        assert snap["counters"]["runs_started"] == 1
        assert snap["counters"]["runs_completed"] == 1
```

- [ ] **Step 2: Run test to verify it fails (HANGS)**

Run: `timeout 15 python -m pytest tests/unit/test_metrics_locking.py -q ; echo "exit=$?"`
Expected: the run **hangs** and `timeout` kills it (`exit=124`) — that IS the bug. Do not interpret the hang as the test passing.

- [ ] **Step 3: Switch the mutex to a re-entrant lock**

In `aether/core/metrics.py`, in `MetricsCollector.__init__` (:44), change:

```python
        self._mutex = threading.Lock()
```

to:

```python
        self._mutex = threading.RLock()  # re-entrant: record_step/start_run call inc()/observe() while held
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 15 python -m pytest tests/unit/test_metrics_locking.py -q ; echo "exit=$?"`
Expected: PASS (2 passed), `exit=0`.

- [ ] **Step 5: Compile + lint + commit**

Run: `python -m compileall aether/core/metrics.py && ruff check aether/core/metrics.py tests/unit/test_metrics_locking.py`
Expected: no errors.

```bash
git add aether/core/metrics.py tests/unit/test_metrics_locking.py
git commit -m "fix(metrics): use RLock to stop re-entrant deadlock in record_step/start_run"
```

---

## Task A1: Token capture in `LLMResponse` + per-backend usage extractors

**Files:**
- Modify: `aether/core/llm.py` (`LLMResponse` dataclass at :43-49; `LLM.step` :116-122; `OpenAICompatibleClient.step` :259-265; `LocalHTTPClient.step` :403-417)
- Test: `tests/unit/test_llm_usage.py` (create)

**Interfaces:**
- Produces: `LLMResponse.input_tokens: int | None`, `LLMResponse.output_tokens: int | None`, `LLMResponse.cost_usd: float | None` (all default `None`).
- Produces: `_usage_from_anthropic(resp) -> tuple[int|None, int|None]`, `_usage_from_openai(resp) -> tuple[int|None, int|None]`, `_usage_from_ollama(body: dict) -> tuple[int|None, int|None]`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_llm_usage.py`:

```python
"""Unit tests for per-backend token-usage extraction (Phase 1)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from aether.core.llm import (
    LLMResponse,
    _usage_from_anthropic,
    _usage_from_ollama,
    _usage_from_openai,
)


@pytest.mark.unit
class TestUsageExtractors:
    def test_anthropic_usage(self) -> None:
        resp = SimpleNamespace(usage=SimpleNamespace(input_tokens=12, output_tokens=34))
        assert _usage_from_anthropic(resp) == (12, 34)

    def test_anthropic_usage_missing(self) -> None:
        assert _usage_from_anthropic(SimpleNamespace()) == (None, None)

    def test_openai_usage(self) -> None:
        resp = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=5, completion_tokens=7))
        assert _usage_from_openai(resp) == (5, 7)

    def test_openai_usage_none(self) -> None:
        resp = SimpleNamespace(usage=None)
        assert _usage_from_openai(resp) == (None, None)

    def test_ollama_native_counts(self) -> None:
        body = {"prompt_eval_count": 11, "eval_count": 22, "message": {"content": "hi"}}
        assert _usage_from_ollama(body) == (11, 22)

    def test_ollama_openai_style_usage(self) -> None:
        body = {"usage": {"prompt_tokens": 3, "completion_tokens": 4}}
        assert _usage_from_ollama(body) == (3, 4)

    def test_ollama_no_usage(self) -> None:
        assert _usage_from_ollama({"message": {"content": "hi"}}) == (None, None)


@pytest.mark.unit
class TestLLMResponseFields:
    def test_defaults_none(self) -> None:
        r = LLMResponse(text="x", tool_calls=[], raw_content=[], stop_reason="end_turn")
        assert r.input_tokens is None
        assert r.output_tokens is None
        assert r.cost_usd is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_llm_usage.py -q`
Expected: FAIL — `ImportError: cannot import name '_usage_from_anthropic'`.

- [ ] **Step 3: Add fields + extractors**

In `aether/core/llm.py`, replace the `LLMResponse` dataclass (currently :43-49) with:

```python
@dataclass
class LLMResponse:
    text: str                       # any assistant text in this turn
    tool_calls: list[dict]          # [{id, name, input}]
    raw_content: list               # original content blocks (to echo back)
    stop_reason: str | None
    backend: str = "anthropic"
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
```

Add these three pure helpers near the other module-level helpers (e.g. just above `_format_tools_for_local` at :516):

```python
def _usage_from_anthropic(resp: Any) -> tuple[int | None, int | None]:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return (None, None)
    return (getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None))


def _usage_from_openai(resp: Any) -> tuple[int | None, int | None]:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return (None, None)
    return (getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))


def _usage_from_ollama(body: dict) -> tuple[int | None, int | None]:
    if not isinstance(body, dict):
        return (None, None)
    if "prompt_eval_count" in body or "eval_count" in body:
        return (body.get("prompt_eval_count"), body.get("eval_count"))
    usage = body.get("usage")
    if isinstance(usage, dict):
        return (usage.get("prompt_tokens"), usage.get("completion_tokens"))
    return (None, None)
```

- [ ] **Step 4: Populate usage at the three backends**

In `LLM.step`, change the `return LLMResponse(...)` (:116-122) to:

```python
        in_tok, out_tok = _usage_from_anthropic(resp)
        return LLMResponse(
            text="\n".join(text_parts).strip(),
            tool_calls=tool_calls,
            raw_content=resp.content,
            stop_reason=resp.stop_reason,
            backend="anthropic",
            input_tokens=in_tok,
            output_tokens=out_tok,
        )
```

In `OpenAICompatibleClient.step`, change the `return LLMResponse(...)` (:259-265) to:

```python
        in_tok, out_tok = _usage_from_openai(resp)
        return LLMResponse(
            text=text if not tool_calls else "",
            tool_calls=tool_calls,
            raw_content=raw_blocks,
            stop_reason=stop,
            backend=self.backend,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )
```

In `LocalHTTPClient.step`, change the `return LLMResponse(...)` (:411-417) to:

```python
        in_tok, out_tok = _usage_from_ollama(body)
        return LLMResponse(
            text=text if not tool_calls else "",
            tool_calls=tool_calls,
            raw_content=raw,
            stop_reason="tool_use" if tool_calls else "end_turn",
            backend="local_http",
            input_tokens=in_tok,
            output_tokens=out_tok,
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_llm_usage.py -q`
Expected: PASS (10 passed).

- [ ] **Step 6: Compile + lint + commit**

Run: `python -m compileall aether && ruff check aether/core/llm.py tests/unit/test_llm_usage.py`
Expected: no errors.

```bash
git add aether/core/llm.py tests/unit/test_llm_usage.py
git commit -m "feat(router): capture token usage from all LLM backends"
```

---

## Task A2: Cost accounting in `MetricsCollector`

**Files:**
- Modify: `aether/core/metrics.py` (`RunMetrics` :23-34; `MetricsCollector.__init__` :43-51; `snapshot` :124-170)
- Modify: `tests/conftest.py` (add metrics reset fixture)
- Test: `tests/unit/test_metrics.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks (pure addition).
- Produces: `estimate_cost_usd(provider: str, model: str | None, tokens_in: int, tokens_out: int) -> float`.
- Produces: `MetricsCollector.record_llm_usage(provider: str, tokens_in: int | None, tokens_out: int | None, model: str | None = None) -> None`.
- Produces: `snapshot()['provider_costs']: dict[str, dict]` (per-provider `{tokens_in, tokens_out, cost_usd, calls}`) and `snapshot()['total_cost_usd']: float`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_metrics.py`:

```python
"""Unit tests for token/cost accounting (Phase 1)."""
from __future__ import annotations

import pytest

from aether.core.metrics import MetricsCollector, estimate_cost_usd


@pytest.mark.unit
class TestEstimateCost:
    def test_known_provider_model(self) -> None:
        # claude-sonnet-4-6: 0.003 in + 0.015 out per 1K → 1K each = 0.018
        cost = estimate_cost_usd("anthropic", "claude-sonnet-4-6", 1000, 1000)
        assert cost == pytest.approx(0.018, rel=1e-6)

    def test_local_is_free(self) -> None:
        assert estimate_cost_usd("local_http", None, 5000, 5000) == 0.0

    def test_unknown_provider_zero(self) -> None:
        assert estimate_cost_usd("totally_unknown", None, 1000, 1000) == 0.0

    def test_provider_fallback_when_model_unknown(self) -> None:
        # provider known, model unknown → provider fallback price applies (non-zero)
        assert estimate_cost_usd("anthropic", "some-future-model", 1000, 0) > 0.0


@pytest.mark.unit
class TestRecordUsage:
    def test_accumulates_provider_costs(self) -> None:
        m = MetricsCollector()
        m.record_llm_usage("anthropic", 1000, 1000, model="claude-sonnet-4-6")
        snap = m.snapshot()
        pc = snap["provider_costs"]["anthropic"]
        assert pc["tokens_in"] == 1000
        assert pc["tokens_out"] == 1000
        assert pc["calls"] == 1
        assert pc["cost_usd"] == pytest.approx(0.018, rel=1e-6)
        assert snap["total_cost_usd"] == pytest.approx(0.018, rel=1e-6)
        assert snap["counters"]["tokens_in_anthropic"] == 1000

    def test_unknown_provider_counter(self) -> None:
        m = MetricsCollector()
        m.record_llm_usage("weirdprov", 10, 10)
        snap = m.snapshot()
        assert snap["counters"]["unknown_provider_usage"] == 1
        assert snap["total_cost_usd"] == 0.0

    def test_none_tokens_noop(self) -> None:
        m = MetricsCollector()
        m.record_llm_usage("anthropic", None, None, model="claude-sonnet-4-6")
        snap = m.snapshot()
        assert "anthropic" not in snap["provider_costs"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_metrics.py -q`
Expected: FAIL — `ImportError: cannot import name 'estimate_cost_usd'`.

- [ ] **Step 3: Add price table + cost helper**

In `aether/core/metrics.py`, add after `LATENCY_BUDGETS_MS` (:20):

```python
# Estimated LLM prices in USD per 1K tokens (input, output). ESTIMATES ONLY —
# token counts from providers can differ from billed amounts; cache/tiered
# pricing is ignored. Keyed by provider (LLMResponse.backend); MODEL_PRICES
# overrides per model where known. local_http is free.
PROVIDER_PRICES: dict[str, dict[str, float]] = {
    "anthropic": {"in": 0.003, "out": 0.015},
    "openai": {"in": 0.00015, "out": 0.0006},
    "google": {"in": 0.0001, "out": 0.0004},
    "groq": {"in": 0.00059, "out": 0.00079},
    "fireworks": {"in": 0.0009, "out": 0.0009},
    "openrouter": {"in": 0.003, "out": 0.015},
    "kilo": {"in": 0.003, "out": 0.015},
    "kie": {"in": 0.00125, "out": 0.005},
    "zai": {"in": 0.0006, "out": 0.0022},
    "zai_vision": {"in": 0.0006, "out": 0.0022},
    "local_http": {"in": 0.0, "out": 0.0},
}

MODEL_PRICES: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"in": 0.003, "out": 0.015},
    "gpt-4o-mini": {"in": 0.00015, "out": 0.0006},
    "gemini-2.0-flash": {"in": 0.0001, "out": 0.0004},
    "llama-3.3-70b-versatile": {"in": 0.00059, "out": 0.00079},
    "glm-5-turbo": {"in": 0.0006, "out": 0.0022},
    "glm-5v-turbo": {"in": 0.0006, "out": 0.0022},
}


def estimate_cost_usd(
    provider: str,
    model: str | None,
    tokens_in: int,
    tokens_out: int,
) -> float:
    """Estimated USD cost for a turn. 0.0 for local/unknown providers."""
    rate = None
    if model and model in MODEL_PRICES:
        rate = MODEL_PRICES[model]
    elif provider in PROVIDER_PRICES:
        rate = PROVIDER_PRICES[provider]
    if rate is None:
        return 0.0
    return (tokens_in / 1000.0) * rate["in"] + (tokens_out / 1000.0) * rate["out"]
```

- [ ] **Step 4: Add `RunMetrics` fields + accumulator + `record_llm_usage` + snapshot keys**

In `RunMetrics` (:23-34), add three fields after `step_latencies_ms`:

```python
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
```

In `MetricsCollector.__init__` (:43-51), add after `self._max_hist = 200`:

```python
        self._provider_costs: dict[str, dict[str, float]] = {}
```

Add this method after `record_tool` (after :122). **Important:** `inc()` acquires `self._mutex` itself and `threading.Lock` is non-reentrant, so call `inc()` BEFORE taking the lock for the dict updates — never nest the lock:

```python
    def record_llm_usage(
        self,
        provider: str,
        tokens_in: int | None,
        tokens_out: int | None,
        model: str | None = None,
    ) -> None:
        if tokens_in is None and tokens_out is None:
            return
        ti = int(tokens_in or 0)
        to = int(tokens_out or 0)
        known = provider in PROVIDER_PRICES or (model is not None and model in MODEL_PRICES)
        cost = estimate_cost_usd(provider, model, ti, to)
        self.inc(f"tokens_in_{provider}", ti)
        self.inc(f"tokens_out_{provider}", to)
        if not known:
            self.inc("unknown_provider_usage")
        with self._mutex:
            entry = self._provider_costs.setdefault(
                provider, {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "calls": 0}
            )
            entry["tokens_in"] += ti
            entry["tokens_out"] += to
            entry["cost_usd"] += cost
            entry["calls"] += 1
            if self._current_run:
                self._current_run.tokens_in += ti
                self._current_run.tokens_out += to
                self._current_run.cost_usd += cost
```

In `snapshot()`, add two keys to the returned dict (after `"slow_warnings": ...` at :169, inside the dict literal):

```python
                "provider_costs": {
                    p: dict(v) for p, v in self._provider_costs.items()
                },
                "total_cost_usd": round(
                    sum(v["cost_usd"] for v in self._provider_costs.values()), 6
                ),
```

- [ ] **Step 5: Add the metrics reset fixture**

In `tests/conftest.py`, add after the `_reset_mcp_active_client` fixture (after :33):

```python
@pytest.fixture(autouse=True)
def _reset_metrics() -> Generator[None, None, None]:
    """Reset the MetricsCollector singleton so counters don't leak across tests."""
    from aether.core.metrics import MetricsCollector

    MetricsCollector._instance = None  # noqa: SLF001
    yield
    MetricsCollector._instance = None  # noqa: SLF001
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_metrics.py -q`
Expected: PASS (7 passed).

- [ ] **Step 7: Full unit run + lint + commit**

Run: `python -m pytest tests/unit -q && ruff check aether/core/metrics.py tests/unit/test_metrics.py tests/conftest.py`
Expected: PASS, no lint errors.

```bash
git add aether/core/metrics.py tests/unit/test_metrics.py tests/conftest.py
git commit -m "feat(router): per-provider token + estimated cost accounting"
```

---

## Task A3: Wire usage recording into the orchestrator

**Files:**
- Modify: `aether/core/orchestrator.py` (`_reason_step`, the `return resp, decision.tier.value` at :254)
- Test: `tests/unit/test_reason_usage.py` (create)

**Interfaces:**
- Consumes: `LLMResponse.input_tokens/output_tokens` (Task A1); `MetricsCollector.record_llm_usage` (Task A2).
- Produces: usage recorded once per `_reason_step` return (covers primary call, local→cloud fallback, vision-tier reasoning, and the self-correction turn — all route through `_reason_step`).

> **Design note:** Recording **inside** `_reason_step` before its single `return` captures every code path that produces a `resp` (primary, fallback, self-correction). The only LLM call NOT covered is the auxiliary `VisionLLM.analyze_screenshot` VLM description call, which returns a string, not an `LLMResponse`, and whose underlying `analyze_image` does not surface usage — documented as a known limitation, out of scope.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_reason_usage.py`:

```python
"""Unit test: _reason_step records token usage (Phase 1)."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from aether.core.llm import LLMResponse


@dataclass
class _UsageClient:
    def step(self, system, messages, tools, *, abort_event=None) -> LLMResponse:  # noqa: ANN001
        return LLMResponse(
            text="done", tool_calls=[], raw_content=[], stop_reason="end_turn",
            backend="anthropic", input_tokens=100, output_tokens=50,
        )


@pytest.mark.unit
def test_record_usage_for_response_helper() -> None:
    from aether.core.metrics import MetricsCollector
    from aether.core.orchestrator import record_usage_for_response

    m = MetricsCollector()
    resp = _UsageClient().step("s", [], [])
    record_usage_for_response(m, resp)
    snap = m.snapshot()
    assert snap["provider_costs"]["anthropic"]["tokens_in"] == 100
    assert snap["provider_costs"]["anthropic"]["tokens_out"] == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_reason_usage.py -q`
Expected: FAIL — `ImportError: cannot import name 'record_usage_for_response'`.

- [ ] **Step 3: Add the helper + call it in `_reason_step`**

In `aether/core/orchestrator.py`, add a module-level helper (place near the top of the file, after the imports, before the `Agent` class):

```python
def record_usage_for_response(metrics, resp) -> None:  # noqa: ANN001
    """Record token usage for an LLMResponse if the backend reported any."""
    if resp is None:
        return
    in_tok = getattr(resp, "input_tokens", None)
    out_tok = getattr(resp, "output_tokens", None)
    if in_tok is None and out_tok is None:
        return
    try:
        metrics.record_llm_usage(getattr(resp, "backend", "unknown"), in_tok, out_tok)
    except Exception:  # noqa: BLE001 — metrics must never break the agent loop
        pass
```

In `_reason_step`, change the final `return resp, decision.tier.value` (:254) to:

```python
        record_usage_for_response(self.metrics, resp)
        return resp, decision.tier.value
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_reason_usage.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Compile + lint + commit**

Run: `python -m compileall aether/core/orchestrator.py && ruff check aether/core/orchestrator.py tests/unit/test_reason_usage.py`
Expected: no errors.

```bash
git add aether/core/orchestrator.py tests/unit/test_reason_usage.py
git commit -m "feat(router): record LLM token usage in the agent loop"
```

---

## Task A4: Cost panel on the sidecar dashboard

**Files:**
- Modify: `sidecar/server.py` (`dashboard` :526-561)
- Test: `tests/integration/test_dashboard_cost.py` (create)

**Interfaces:**
- Consumes: `snapshot()['provider_costs']`, `snapshot()['total_cost_usd']` (Task A2).
- Produces: a "Cost & tokens (estimated)" table in the `/dashboard` HTML.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_dashboard_cost.py`:

```python
"""Integration: /dashboard renders the estimated cost panel (Phase 1)."""
from __future__ import annotations

import pytest


@pytest.mark.integration
def test_dashboard_has_cost_panel(sidecar_client) -> None:  # noqa: ANN001
    from aether.core.metrics import MetricsCollector

    MetricsCollector.get().record_llm_usage("anthropic", 1000, 500, model="claude-sonnet-4-6")
    resp = sidecar_client.get("/dashboard")
    assert resp.status_code == 200
    body = resp.text
    assert "Cost &amp; tokens" in body or "Cost & tokens" in body
    assert "anthropic" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_dashboard_cost.py -q`
Expected: FAIL — assertion error (`anthropic` / cost panel not in body).

- [ ] **Step 3: Render the cost panel**

In `sidecar/server.py`, inside `dashboard()`, after the `counter_rows = ...` block (:543-545) add:

```python
    provider_costs = snap.get("provider_costs", {})
    total_cost = snap.get("total_cost_usd", 0.0)
    cost_rows = "".join(
        f"<tr><td>{p}</td><td>{c.get('tokens_in')}</td>"
        f"<td>{c.get('tokens_out')}</td><td>{c.get('calls')}</td>"
        f"<td>${c.get('cost_usd', 0.0):.4f}</td></tr>"
        for p, c in sorted(provider_costs.items())
    )
    cost_rows += (
        f"<tr><td><b>TOTAL</b></td><td></td><td></td><td></td>"
        f"<td><b>${total_cost:.4f}</b></td></tr>"
    )
```

In the returned f-string, add a new section right before `<h2>Recent runs</h2>` (:560):

```python
<h2>Cost &amp; tokens (estimated)</h2>
<p class="meta">Estimates from provider token counts — not billing-accurate.</p>
<table><tr><th>Provider</th><th>Tokens in</th><th>Tokens out</th><th>Calls</th><th>Cost USD</th></tr>{cost_rows}</table>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_dashboard_cost.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Compile + lint + commit**

Run: `python -m compileall sidecar/server.py && ruff check sidecar/server.py tests/integration/test_dashboard_cost.py`
Expected: no errors.

```bash
git add sidecar/server.py tests/integration/test_dashboard_cost.py
git commit -m "feat(sidecar): estimated cost & tokens panel on /dashboard"
```

---

## Task A5: Native Ollama tool-calling for the local model

**Files:**
- Modify: `aether/core/llm.py` (`LocalHTTPClient.__init__` :320-333; `LocalHTTPClient.step` :378-417; add `_parse_ollama_tool_calls`)
- Modify: `aether/core/router.py` (`_get_or_create_local` :267-277)
- Modify: `configs/router.yaml` (`roles.local_fast`)
- Test: `tests/unit/test_local_native_tools.py` (create)

**Interfaces:**
- Consumes: `_anthropic_tools_to_openai` (existing, :559) — reused as the Ollama `tools` formatter (identical shape, DRY).
- Produces: `_parse_ollama_tool_calls(body: dict) -> list[dict]` returning `[{id, name, input}]`.
- Produces: `LocalHTTPClient(..., native_tools: bool = False)`; router passes `native_tools` from `roles.local_fast.native_tools`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_local_native_tools.py`:

```python
"""Unit tests for native Ollama tool-calling (Phase 1)."""
from __future__ import annotations

import pytest

from aether.core.llm import LocalHTTPClient, _parse_ollama_tool_calls


@pytest.mark.unit
class TestParseOllamaToolCalls:
    def test_parses_dict_arguments(self) -> None:
        body = {"message": {"tool_calls": [
            {"function": {"name": "open_app", "arguments": {"name": "Finder"}}}
        ]}}
        calls = _parse_ollama_tool_calls(body)
        assert len(calls) == 1
        assert calls[0]["name"] == "open_app"
        assert calls[0]["input"] == {"name": "Finder"}
        assert calls[0]["id"].startswith("local_")

    def test_parses_string_arguments(self) -> None:
        body = {"message": {"tool_calls": [
            {"function": {"name": "click", "arguments": "{\"element_index\": 3}"}}
        ]}}
        calls = _parse_ollama_tool_calls(body)
        assert calls[0]["input"] == {"element_index": 3}

    def test_no_tool_calls(self) -> None:
        assert _parse_ollama_tool_calls({"message": {"content": "hello"}}) == []

    def test_native_tools_flag_defaults_false(self) -> None:
        c = LocalHTTPClient()
        assert c.native_tools is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_local_native_tools.py -q`
Expected: FAIL — `ImportError: cannot import name '_parse_ollama_tool_calls'`.

- [ ] **Step 3: Add the parser + `native_tools` flag + native payload path**

In `aether/core/llm.py`, add after `_json_to_tool_call` (after :556):

```python
def _parse_ollama_tool_calls(body: dict) -> list[dict]:
    """Parse Ollama native message.tool_calls into [{id, name, input}]."""
    msg = body.get("message") if isinstance(body, dict) else None
    if not isinstance(msg, dict):
        return []
    calls: list[dict] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") if isinstance(tc, dict) else None
        if not isinstance(fn, dict):
            continue
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        calls.append({
            "id": f"local_{uuid.uuid4().hex[:12]}",
            "name": fn.get("name", ""),
            "input": dict(args or {}),
        })
    return calls
```

In `LocalHTTPClient.__init__` (:320-333), add a parameter and store it. Change the signature/body to include:

```python
    def __init__(
        self,
        endpoint: str = "http://localhost:11434/api/chat",
        model: str = "llama3.2:3b",
        max_tokens: int = 512,
        temperature: float = 0.0,
        timeout: float = 30.0,
        native_tools: bool = False,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self.native_tools = native_tools
        self._available: bool | None = None
```

In `LocalHTTPClient.step`, replace the tool-hint + payload + parse section (currently :378-409) with:

```python
        payload = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": False,
            "options": {"temperature": self.temperature, "num_predict": self.max_tokens},
        }
        if self.native_tools and tools:
            payload["tools"] = _anthropic_tools_to_openai(tools)
        else:
            tool_hint = _format_tools_for_local(tools)
            if tool_hint:
                ollama_messages[0]["content"] += "\n\n" + tool_hint

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            _check_abort(abort_event)
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            log.warning("Local model call failed: %s", e)
            raise RuntimeError(f"Local model unavailable: {e}") from e

        text = ""
        if "message" in body:
            text = body["message"].get("content", "") or ""
        elif "choices" in body:
            text = body["choices"][0]["message"].get("content", "") or ""

        tool_calls = _parse_ollama_tool_calls(body) if self.native_tools else []
        if not tool_calls:
            tool_calls = _parse_tool_calls_from_text(text)
```

(The `in_tok, out_tok = _usage_from_ollama(body)` + `return LLMResponse(...)` lines from Task A1 follow unchanged.)

- [ ] **Step 4: Pass `native_tools` from router config**

In `aether/core/router.py`, change `_get_or_create_local` (:267-277) to read the flag:

```python
    def _get_or_create_local(self) -> LocalHTTPClient:
        if "local_fast" not in self._clients:
            role = self.cfg.role_config("local_fast")
            self._clients["local_fast"] = LocalHTTPClient(
                endpoint=str(role.get("endpoint", "http://localhost:11434/api/chat")),
                model=str(role.get("model", "llama3.2:3b")),
                max_tokens=int(role.get("max_tokens", 512)),
                temperature=float(role.get("temperature", 0)),
                timeout=float(role.get("timeout_seconds", 30)),
                native_tools=bool(role.get("native_tools", False)),
            )
        return self._clients["local_fast"]
```

- [ ] **Step 5: Add the config flag**

In `configs/router.yaml`, under `roles.local_fast` (the block at :119-125), add one line:

```yaml
  local_fast:
    backend: http          # http | anthropic | openai_compatible | google
    endpoint: http://localhost:11434/api/chat   # Ollama-compatible
    model: llama3.2:3b
    max_tokens: 512
    temperature: 0
    timeout_seconds: 30
    native_tools: false    # true → use Ollama native /api/chat tools field (Phase 1)
```

- [ ] **Step 6: Run tests + verify routing tests still pass**

Run: `python -m pytest tests/unit/test_local_native_tools.py tests/unit/test_router.py tests/unit/test_router_failover.py -q`
Expected: PASS (all).

- [ ] **Step 7: Compile + lint + commit**

Run: `python -m compileall aether && ruff check aether/core/llm.py aether/core/router.py tests/unit/test_local_native_tools.py`
Expected: no errors.

```bash
git add aether/core/llm.py aether/core/router.py configs/router.yaml tests/unit/test_local_native_tools.py
git commit -m "feat(router): native Ollama tool-calling for local model (gated, JSON fallback retained)"
```

---

# Milestone B — Vision Fallback

## Task B1: Screen-content classifier + single-pass `recognize`

**Files:**
- Modify: `aether/perception/ocr.py` (refactor `recognize_text_formatted` :86-100; add `_format_regions`, `recognize`, `classify_screen_content`)
- Test: `tests/unit/test_vision.py` (create)

**Interfaces:**
- Produces: `classify_screen_content(regions: list[TextRegion], image_w: int, image_h: int, *, min_conf: float = 0.3) -> dict` with keys `label` (`'text_heavy'|'sparse'|'graphical'|'empty'|'unknown'`), `score`, `char_count`, `text_coverage`, `region_count`, `mean_confidence`.
- Produces: `recognize(image_path: str) -> tuple[str, list[TextRegion], tuple[int, int]]` (formatted, raw normalized regions, dims).
- Produces: `_format_regions(regions, w, h, limit=40) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_vision.py`:

```python
"""Unit tests for the screen-content classifier (Phase 1)."""
from __future__ import annotations

import pytest

from aether.perception.ocr import TextRegion, classify_screen_content


def _region(text: str, conf: float, w: float, h: float) -> TextRegion:
    return TextRegion(text=text, confidence=conf, x=0.1, y=0.1, w=w, h=h)


@pytest.mark.unit
class TestClassifier:
    def test_text_heavy(self) -> None:
        regions = [_region("a paragraph of words " * 2, 0.95, 0.4, 0.03) for _ in range(20)]
        out = classify_screen_content(regions, 1000, 800)
        assert out["label"] == "text_heavy"
        assert out["char_count"] >= 200

    def test_empty_when_no_regions(self) -> None:
        out = classify_screen_content([], 1000, 800)
        assert out["label"] == "empty"

    def test_graphical_few_tiny_boxes(self) -> None:
        regions = [_region("x", 0.6, 0.001, 0.001) for _ in range(2)]
        out = classify_screen_content(regions, 1000, 800)
        assert out["label"] == "graphical"

    def test_unknown_when_dims_invalid(self) -> None:
        regions = [_region("hello world", 0.9, 0.2, 0.05)]
        out = classify_screen_content(regions, 0, 0)
        assert out["label"] == "unknown"

    def test_low_confidence_filtered(self) -> None:
        regions = [_region("noise " * 50, 0.1, 0.5, 0.5) for _ in range(10)]
        out = classify_screen_content(regions, 1000, 800)
        # all below min_conf → filtered → empty
        assert out["region_count"] == 0
        assert out["label"] == "empty"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_vision.py -q`
Expected: FAIL — `ImportError: cannot import name 'classify_screen_content'`.

- [ ] **Step 3: Add `classify_screen_content`**

In `aether/perception/ocr.py`, add at the end of the file:

```python
def classify_screen_content(
    regions: list[TextRegion],
    image_w: int,
    image_h: int,
    *,
    min_conf: float = 0.3,
) -> dict[str, Any]:
    """Classify a screen as text_heavy / sparse / graphical / empty / unknown.

    `regions` are normalized (0–1) TextRegions as returned by recognize_text.
    Pure function — no I/O. Filters low-confidence boxes (Vision emits spurious
    boxes on graphics). 'unknown' when image dims are unavailable.
    """
    kept = [r for r in regions if r.confidence >= min_conf]
    region_count = len(kept)
    char_count = sum(len(r.text) for r in kept)
    mean_conf = (sum(r.confidence for r in kept) / region_count) if region_count else 0.0
    coverage = min(sum(max(r.w, 0.0) * max(r.h, 0.0) for r in kept), 1.0)

    base = {
        "char_count": char_count,
        "text_coverage": round(coverage, 4),
        "region_count": region_count,
        "mean_confidence": round(mean_conf, 3),
    }
    if image_w <= 0 or image_h <= 0:
        return {"label": "unknown", "score": 0.0, **base}
    if region_count == 0 or char_count == 0:
        return {"label": "empty", "score": 0.0, **base}

    score = min(1.0, (char_count / 400.0) * 0.6 + coverage * 0.4)
    if char_count >= 200 and coverage >= 0.05 and mean_conf >= 0.4:
        label = "text_heavy"
    elif coverage < 0.02 and char_count < 60:
        label = "graphical"
    else:
        label = "sparse"
    return {"label": label, "score": round(score, 3), **base}
```

- [ ] **Step 4: Run classifier tests to verify they pass**

Run: `python -m pytest tests/unit/test_vision.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Add `_format_regions` + `recognize` and refactor `recognize_text_formatted` (single OCR pass)**

In `aether/perception/ocr.py`, replace `recognize_text_formatted` (:86-100) with:

```python
def _format_regions(
    regions: list[TextRegion],
    w: int,
    h: int,
    limit: int = 40,
) -> str:
    if not regions:
        return "No text detected." if _VISION_OK else "OCR unavailable (install pyobjc-framework-Vision)."
    if w > 0 and h > 0:
        scaled = scale_regions_to_pixels(regions, w, h)
        lines = [r.describe(pixel_coords=True) for r in scaled[:limit]]
    else:
        lines = [r.describe() for r in regions[:limit]]
    more = f"\n…({len(regions) - limit} more)" if len(regions) > limit else ""
    return f"OCR found {len(regions)} regions:\n" + "\n".join(lines) + more


def recognize_text_formatted(image_path: str, limit: int = 40) -> str:
    """OCR with a compact string for LLM context."""
    regions = recognize_text(image_path)
    if not regions:
        if not _VISION_OK:
            return "OCR unavailable (install pyobjc-framework-Vision)."
        return "No text detected."
    w, h = image_dimensions(image_path)
    return _format_regions(regions, w, h, limit)


def recognize(image_path: str) -> tuple[str, list[TextRegion], tuple[int, int]]:
    """Single OCR pass: returns (formatted_string, raw_regions, (w, h))."""
    regions = recognize_text(image_path)
    w, h = image_dimensions(image_path)
    return _format_regions(regions, w, h), regions, (w, h)
```

- [ ] **Step 6: Add a `recognize` smoke test**

Append to `tests/unit/test_vision.py`:

```python
@pytest.mark.unit
def test_recognize_returns_triple(monkeypatch) -> None:  # noqa: ANN001
    import aether.perception.ocr as ocr_mod

    fake = [TextRegion("hi", 0.9, 0.1, 0.1, 0.2, 0.05)]
    monkeypatch.setattr(ocr_mod, "recognize_text", lambda p: fake)
    monkeypatch.setattr(ocr_mod, "image_dimensions", lambda p: (1000, 800))
    formatted, regions, dims = ocr_mod.recognize("x.png")
    assert "OCR found 1 regions" in formatted
    assert regions == fake
    assert dims == (1000, 800)
```

- [ ] **Step 7: Run + lint + commit**

Run: `python -m pytest tests/unit/test_vision.py -q && ruff check aether/perception/ocr.py tests/unit/test_vision.py`
Expected: PASS, no lint errors.

```bash
git add aether/perception/ocr.py tests/unit/test_vision.py
git commit -m "feat(vision): screen-content classifier + single-pass recognize()"
```

---

## Task B2: Auto OCR-only on text-heavy screens

**Files:**
- Modify: `aether/perception/vision.py` (`analyze_screen` :12-35)
- Modify: `aether/core/llm.py` (`VisionLLM.__init__` :423-434; `VisionLLM.analyze_screenshot` :451-484)
- Test: `tests/unit/test_vision_routing.py` (create)

**Interfaces:**
- Consumes: `ocr.recognize` + `ocr.classify_screen_content` (Task B1).
- Produces: `vision.analyze_screen(...)` result dict gains `content_class` + `content`; skips `cloud_analyze_fn` when `text_heavy`.
- Produces: `VisionLLM.last_content_class: str`; `analyze_screenshot` skips the VLM when `text_heavy`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_vision_routing.py`:

```python
"""Unit tests: vision auto OCR-only on text-heavy screens (Phase 1)."""
from __future__ import annotations

import pytest

from aether.perception import vision
from aether.perception.ocr import TextRegion


def _heavy() -> list[TextRegion]:
    return [TextRegion("a paragraph of words " * 2, 0.95, 0.1, 0.1, 0.4, 0.03) for _ in range(20)]


def _sparse() -> list[TextRegion]:
    return [TextRegion("Go", 0.9, 0.1, 0.1, 0.05, 0.03)]


@pytest.mark.unit
class TestAnalyzeScreenAutoOCR:
    def test_text_heavy_skips_cloud(self, monkeypatch) -> None:  # noqa: ANN001
        import aether.perception.ocr as ocr_mod

        monkeypatch.setattr(ocr_mod, "recognize", lambda p: ("fmt", _heavy(), (1000, 800)))
        called = {"n": 0}

        def _cloud(_path: str) -> str:
            called["n"] += 1
            return "vlm"

        out = vision.analyze_screen("x.png", use_cloud=True, cloud_analyze_fn=_cloud)
        assert out["content_class"] == "text_heavy"
        assert called["n"] == 0  # cloud VLM skipped

    def test_sparse_uses_cloud(self, monkeypatch) -> None:  # noqa: ANN001
        import aether.perception.ocr as ocr_mod

        monkeypatch.setattr(ocr_mod, "recognize", lambda p: ("fmt", _sparse(), (1000, 800)))
        called = {"n": 0}

        def _cloud(_path: str) -> str:
            called["n"] += 1
            return "vlm"

        out = vision.analyze_screen("x.png", use_cloud=True, cloud_analyze_fn=_cloud)
        assert out["content_class"] != "text_heavy"
        assert called["n"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_vision_routing.py -q`
Expected: FAIL — `KeyError: 'content_class'`.

- [ ] **Step 3: Rewrite `vision.analyze_screen`**

Replace the body of `analyze_screen` in `aether/perception/vision.py` (:12-35) with:

```python
def analyze_screen(
    image_path: str | None = None,
    *,
    use_cloud: bool = False,
    cloud_analyze_fn: Any = None,
) -> dict[str, Any]:
    """Capture or analyze a screenshot.

    Runs OCR once, classifies the screen, and only calls the cloud VLM when the
    screen is NOT text-heavy (auto OCR-only saves latency/cost on dense text).
    Returns dict with path, ocr_formatted, regions, content_class, and optional
    cloud_analysis.
    """
    path = image_path or screen_cap.try_capture_to_file()
    if path is None:
        return {
            "path": None,
            "ocr_formatted": "Screen capture unavailable (permission?).",
            "regions": [],
            "region_count": 0,
            "content_class": "unknown",
        }
    formatted, regions, (w, h) = ocr.recognize(path)
    content = ocr.classify_screen_content(regions, w, h)
    result: dict[str, Any] = {
        "path": path,
        "ocr_formatted": formatted,
        "regions": ocr.regions_to_dicts(regions, image_path=path, scale_pixels=True),
        "region_count": len(regions),
        "content_class": content["label"],
        "content": content,
    }
    text_heavy = content["label"] == "text_heavy"
    if use_cloud and cloud_analyze_fn is not None and not text_heavy:
        try:
            result["cloud_analysis"] = cloud_analyze_fn(path)
        except Exception as e:  # noqa: BLE001
            result["cloud_analysis_error"] = str(e)
    return result
```

> **Execution order:** this references `screen_cap.try_capture_to_file`, which is added in **Task B4 — complete Task B4 before this task.** (The Task B2 unit test passes `image_path` explicitly so it never calls capture; but the agent-loop path needs the hardened helper present.)

- [ ] **Step 4: Update `VisionLLM.analyze_screenshot`**

In `aether/core/llm.py`, add to `VisionLLM.__init__` (after :434 `self._last_vision_context = ""`):

```python
        self.last_content_class: str = "unknown"
```

Replace `VisionLLM.analyze_screenshot` (:451-484) with:

```python
    def analyze_screenshot(self, image_path: str, prompt: str | None = None) -> str:
        from ..perception import ocr as ocr_mod

        formatted, regions, (w, h) = ocr_mod.recognize(image_path)
        content = ocr_mod.classify_screen_content(regions, w, h)
        self.last_content_class = content["label"]
        parts = [f"Screenshot: {image_path}", formatted]

        text_heavy = content["label"] == "text_heavy"
        if self.ocr_only or text_heavy:
            self._last_vision_context = "\n".join(parts)
            return self._last_vision_context

        if self.vlm_endpoint and self.vlm_model:
            try:
                vlm = self._call_vlm_http(image_path, prompt or "Describe the UI.")
                parts.append(f"VLM analysis:\n{vlm}")
            except Exception as e:
                parts.append(f"VLM error: {e}")
        else:
            try:
                analyze = getattr(self.cloud, "analyze_image", None)
                if callable(analyze):
                    analysis = analyze(
                        image_path,
                        prompt or ("Describe visible UI elements, buttons, and text. "
                                   "Be concise and actionable."),
                    )
                else:
                    analysis = ""
                if analysis:
                    parts.append(f"Vision analysis:\n{analysis}")
            except Exception as e:
                parts.append(f"Cloud vision error: {e}")

        self._last_vision_context = "\n".join(parts)
        return self._last_vision_context
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_vision_routing.py -q`
Expected: PASS (2 passed). (Requires Task B4's `try_capture_to_file` to exist.)

- [ ] **Step 6: Lint + commit**

Run: `ruff check aether/perception/vision.py aether/core/llm.py tests/unit/test_vision_routing.py`
Expected: no errors.

```bash
git add aether/perception/vision.py aether/core/llm.py tests/unit/test_vision_routing.py
git commit -m "feat(vision): auto OCR-only on text-heavy screens"
```

---

## Task B3: World-model content fields + orchestrator write-back

**Files:**
- Modify: `aether/core/world_model.py` (`WorldModel.__init__` fields after :71)
- Modify: `aether/core/orchestrator.py` (`_maybe_vision_context` :178-187)
- Modify: `tests/conftest.py` (`world` fixture :56-68)
- Test: `tests/unit/test_world_content_fields.py` (create)

**Interfaces:**
- Produces: `WorldModel.screen_content_class: str` (default `"unknown"`), `WorldModel.text_heavy_score: float` (default `0.0`), `WorldModel.ax_text_ratio: float` (default `1.0`).
- Consumes: `VisionLLM.last_content_class` (Task B2); `ocr.recognize` + `classify_screen_content` (Task B1).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_world_content_fields.py`:

```python
"""Unit test: WorldModel carries screen-content fields (Phase 1)."""
from __future__ import annotations

import pytest

from aether.core.world_model import WorldModel


@pytest.mark.unit
def test_content_fields_default_non_triggering() -> None:
    w = WorldModel(ax_cache_ttl_ms=0)
    assert w.screen_content_class == "unknown"
    assert w.text_heavy_score == 0.0
    assert w.ax_text_ratio == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_world_content_fields.py -q`
Expected: FAIL — `AttributeError: 'WorldModel' object has no attribute 'screen_content_class'`.

- [ ] **Step 3: Add the world fields**

In `aether/core/world_model.py`, in `__init__`, add after `self.screen_stream_summary = ""` (:71):

```python
        self.screen_content_class: str = "unknown"
        self.text_heavy_score: float = 0.0
        self.ax_text_ratio: float = 1.0
```

- [ ] **Step 4: Write content class back in `_maybe_vision_context`**

In `aether/core/orchestrator.py`, replace `_maybe_vision_context` (:178-187) with:

```python
    async def _maybe_vision_context(self, decision) -> str | None:
        """Inject vision/OCR context when router picks vision tier."""
        if decision.tier != RouteTier.VISION:
            return None
        path = self.world.capture_screenshot()
        if not path:
            return None
        vision_client = self.router.pick_client(decision)
        if hasattr(vision_client, "analyze_screenshot"):
            ctx = await asyncio.to_thread(vision_client.analyze_screenshot, path)
            self.world.screen_content_class = getattr(
                vision_client, "last_content_class", "unknown"
            )
            return ctx
        from ..perception import ocr
        formatted, regions, (w, h) = await asyncio.to_thread(ocr.recognize, path)
        content = ocr.classify_screen_content(regions, w, h)
        self.world.screen_content_class = content["label"]
        self.world.text_heavy_score = content["score"]
        return formatted
```

- [ ] **Step 5: Add the new fields to the `world` test fixture (non-triggering defaults)**

In `tests/conftest.py`, in the `world` fixture (:56-68), add before `return w`:

```python
    w.screen_content_class = "unknown"
    w.text_heavy_score = 0.0
    w.ax_text_ratio = 1.0
```

- [ ] **Step 6: Run tests (new + existing router) to verify they pass**

Run: `python -m pytest tests/unit/test_world_content_fields.py tests/unit/test_router.py -q`
Expected: PASS (all).

- [ ] **Step 7: Compile + lint + commit**

Run: `python -m compileall aether/core/world_model.py aether/core/orchestrator.py && ruff check aether/core/world_model.py aether/core/orchestrator.py tests/conftest.py tests/unit/test_world_content_fields.py`
Expected: no errors.

```bash
git add aether/core/world_model.py aether/core/orchestrator.py tests/conftest.py tests/unit/test_world_content_fields.py
git commit -m "feat(vision): carry screen-content class on the world model"
```

---

## Task B4: Capture hardening (graceful degrade on permission failure)

**Files:**
- Modify: `aether/perception/screen.py` (add `try_capture_to_file`)
- Modify: `aether/core/world_model.py` (`capture_screenshot` :186-189)
- Test: `tests/unit/test_screen_capture.py` (create)

**Interfaces:**
- Produces: `screen.try_capture_to_file(path: str | None = None) -> str | None` (returns `None` on capture failure, logs once).
- Modifies: `WorldModel.capture_screenshot(self) -> str | None` (now nullable).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_screen_capture.py`:

```python
"""Unit tests: screen capture degrades gracefully (Phase 1)."""
from __future__ import annotations

import subprocess

import pytest

from aether.core.world_model import WorldModel
from aether.perception import screen


@pytest.mark.unit
class TestCaptureHardening:
    def test_try_capture_returns_none_on_failure(self, monkeypatch) -> None:  # noqa: ANN001
        def _boom(*a, **k):  # noqa: ANN002, ANN003
            raise subprocess.CalledProcessError(1, "screencapture")

        monkeypatch.setattr(screen, "capture_to_file", _boom)
        screen._warned_capture = False  # noqa: SLF001
        assert screen.try_capture_to_file() is None

    def test_world_capture_returns_none(self, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(screen, "try_capture_to_file", lambda path=None: None)
        w = WorldModel(ax_cache_ttl_ms=0)
        assert w.capture_screenshot() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_screen_capture.py -q`
Expected: FAIL — `AttributeError: module 'aether.perception.screen' has no attribute 'try_capture_to_file'`.

- [ ] **Step 3: Add `try_capture_to_file`**

In `aether/perception/screen.py`, add `import logging` to the imports and a module logger + guarded helper. After the existing imports (:13) add:

```python
import logging

log = logging.getLogger(__name__)
_warned_capture = False
```

Add after `capture_to_file` (:23):

```python
def try_capture_to_file(path: str | None = None) -> str | None:
    """Capture the screen, returning None (not raising) on failure.

    Degrades gracefully when Screen Recording permission is denied or the
    capture times out, so the vision tier falls back to AX-only context.
    """
    global _warned_capture
    try:
        return capture_to_file(path)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        if not _warned_capture:
            log.warning("Screen capture failed (Screen Recording permission?): %s", e)
            _warned_capture = True
        return None
```

- [ ] **Step 4: Use the guarded helper in `WorldModel.capture_screenshot`**

In `aether/core/world_model.py`, replace `capture_screenshot` (:186-189) with:

```python
    def capture_screenshot(self) -> str | None:
        path = screen_cap.try_capture_to_file()
        if path:
            self.last_screenshot = path
        return path
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_screen_capture.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Compile + lint + commit**

Run: `python -m compileall aether/perception/screen.py aether/core/world_model.py && ruff check aether/perception/screen.py aether/core/world_model.py tests/unit/test_screen_capture.py`
Expected: no errors.

```bash
git add aether/perception/screen.py aether/core/world_model.py tests/unit/test_screen_capture.py
git commit -m "feat(vision): degrade gracefully when screen capture fails"
```

---

## Task B5: Router "AX-present-but-wrong" branch + tunable thresholds

**Files:**
- Modify: `aether/core/router.py` (`Router.route` — insert after the AX-insufficient block at :126-135)
- Modify: `configs/router.yaml` (`routing:` block :150-164)
- Test: `tests/unit/test_router.py` (add cases)

**Interfaces:**
- Consumes: `world.screen_content_class`, `world.element_count`, `world.ax_text_ratio` (Task B3); `routing.ax_text_coverage_threshold`.
- Produces: a VISION route with reason `ax_present_but_wrong` when AX looks sufficient but the screen is text-heavy and AX text coverage is low.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_router.py`, inside `class TestRouter`:

```python
    def test_ax_present_but_wrong_routes_to_vision(self, router: Router, world: WorldModel) -> None:
        world.element_count = 10  # AX looks sufficient
        world.ax_insufficient = False
        world.screen_content_class = "text_heavy"
        world.ax_text_ratio = 0.05  # AX exposes almost no text
        decision = router.route(world)
        assert decision.tier == RouteTier.VISION
        assert "ax_present_but_wrong" in decision.reason

    def test_text_heavy_with_good_ax_stays_local(self, router: Router, world: WorldModel) -> None:
        world.element_count = 10
        world.ax_insufficient = False
        world.screen_content_class = "text_heavy"
        world.ax_text_ratio = 0.9  # AX represents the text fine
        world.is_novel_goal = False
        world.needs_replan = False
        decision = router.route(world)
        assert decision.tier == RouteTier.LOCAL_FAST
```

> The `world` fixture sets `screen_content_class="unknown"` and `ax_text_ratio=1.0` (Task B3), so existing routing tests remain unaffected.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_router.py::TestRouter::test_ax_present_but_wrong_routes_to_vision -q`
Expected: FAIL — decision tier is `LOCAL_FAST`, not `VISION`.

- [ ] **Step 3: Add the branch**

In `aether/core/router.py`, in `route()`, immediately AFTER the existing AX-insufficient → vision block (the `if ax_miss or world.element_count == 0 ...` block ending at :135) and BEFORE the `# Careful mode or novel goal` block (:137), insert:

```python
        # AX present but wrong: AX looks sufficient, but the screen is text-heavy
        # while AX exposes little text (canvas/Electron/doc apps) → use vision.
        ax_text_threshold = float(routing.get("ax_text_coverage_threshold", 0.15))
        if (
            getattr(world, "screen_content_class", "unknown") == "text_heavy"
            and world.element_count >= ax_empty_threshold
            and float(getattr(world, "ax_text_ratio", 1.0)) < ax_text_threshold
        ):
            decision = RouteDecision(
                RouteTier.VISION,
                "ax_present_but_wrong (text_heavy, low ax text coverage)",
                use_vision_context=True,
            )
            self._last_decision = decision
            return decision
```

- [ ] **Step 4: Add the tunables to `router.yaml`**

In `configs/router.yaml`, in the `routing:` block (:150-164), add after `ax_empty_threshold: 3`:

```yaml
  # Vision content-classifier tuning (Phase 1)
  ax_text_coverage_threshold: 0.15   # AX text ratio below this + text_heavy → vision
  text_heavy_char_threshold: 200      # min OCR chars to consider a screen text-heavy
  text_coverage_threshold: 0.05       # min OCR text-area coverage for text-heavy
  min_region_confidence: 0.3          # discard OCR boxes below this confidence
```

- [ ] **Step 5: Run router tests to verify they pass**

Run: `python -m pytest tests/unit/test_router.py -q`
Expected: PASS (all, including the two new cases).

- [ ] **Step 6: Compile + lint + commit**

Run: `python -m compileall aether/core/router.py && ruff check aether/core/router.py tests/unit/test_router.py`
Expected: no errors.

```bash
git add aether/core/router.py configs/router.yaml tests/unit/test_router.py
git commit -m "feat(router): route to vision when AX is present but wrong (text-heavy)"
```

---

# Milestone C — Broader App Coverage

## Task C1: Close the pack cold-start gap (pre-warm) + cache bump

**Files:**
- Modify: `aether/knowledge/loader.py` (`_load_pack_file` `@lru_cache` :104; `resolve_pack_key` :125-140; add `_ensure_prewarmed`)
- Test: `tests/unit/test_loader_prewarm.py` (create)

**Interfaces:**
- Produces: `prewarm_packs() -> int` (loads every pack so embedded `bundle_ids`/`aliases` self-register); called lazily from `resolve_pack_key`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_loader_prewarm.py`:

```python
"""Unit tests: pack pre-warm closes the cold-start selection gap (Phase 1)."""
from __future__ import annotations

import pytest

from aether.knowledge import loader


@pytest.mark.unit
def test_prewarm_registers_yaml_bundle_ids(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    pack = tmp_path / "novelapp.yaml"
    pack.write_text(
        "app: NovelApp\ntier: 2\nbundle_ids:\n  - com.test.NovelApp\naliases:\n  - novelapp\n"
    )
    monkeypatch.setenv("AETHER_PACKS_DIR", str(tmp_path))
    loader._load_pack_file.cache_clear()  # noqa: SLF001
    loader._prewarmed = False  # noqa: SLF001
    loader._BUNDLE_TO_PACK.pop("com.test.NovelApp", None)  # ensure not pre-registered

    key = loader.resolve_pack_key(bundle_id="com.test.NovelApp")
    assert key == "novelapp"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_loader_prewarm.py -q`
Expected: FAIL — `resolve_pack_key` returns `None` (cold-start gap).

- [ ] **Step 3: Bump cache + add pre-warm**

In `aether/knowledge/loader.py`, change the `@lru_cache(maxsize=32)` decorator (:104) to:

```python
@lru_cache(maxsize=128)
```

Add a module-level flag after `_BUNDLE_TO_PACK` (after :68):

```python
_prewarmed = False
```

Add the pre-warm functions after `list_packs` (after :122):

```python
def prewarm_packs() -> int:
    """Load every available pack so its bundle_ids/aliases self-register."""
    count = 0
    for key in list_packs():
        if _load_pack_file(key) is not None:
            count += 1
    return count


def _ensure_prewarmed() -> None:
    global _prewarmed
    if _prewarmed:
        return
    _prewarmed = True
    try:
        prewarm_packs()
    except Exception:  # noqa: BLE001 — selection must still work if one pack is bad
        pass
```

In `resolve_pack_key` (:125-140), add `_ensure_prewarmed()` as the first line of the function body:

```python
def resolve_pack_key(app_name: str = "", bundle_id: str = "") -> str | None:
    """Resolve pack key from display name and/or bundle ID."""
    _ensure_prewarmed()
    if bundle_id:
        key = _BUNDLE_TO_PACK.get(bundle_id.strip())
        if key:
            return key
    name = (app_name or "").strip().lower()
    if not name:
        return None
    if name in _APP_ALIASES:
        return _APP_ALIASES[name]
    # Partial match on aliases
    for alias, key in _APP_ALIASES.items():
        if alias in name or name in alias:
            return key
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_loader_prewarm.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Lint + commit**

Run: `python -m compileall aether/knowledge/loader.py && ruff check aether/knowledge/loader.py tests/unit/test_loader_prewarm.py`
Expected: no errors.

```bash
git add aether/knowledge/loader.py tests/unit/test_loader_prewarm.py
git commit -m "feat(packs): pre-warm loader so new packs self-register by bundle id"
```

---

## Task C2: Apple core-comms packs (Messages, FaceTime, Contacts, Reminders)

**Files:**
- Create: `aether/knowledge/packs/messages.yaml`, `facetime.yaml`, `contacts.yaml`, `reminders.yaml`

**Interfaces:**
- Consumes: pack schema (validator requires `app: str`, `tier: int ∈ 0..3`; optional `shortcuts`/`gotchas`/`bundle_ids`/`aliases` lists, `recipes`/`scripting` mappings) and the pre-warm path (Task C1).

> **Before committing:** verify each bundle id on the build machine with `osascript -e 'id of app "Messages"'` etc., and correct the YAML if it differs.

- [ ] **Step 1: Create `messages.yaml`**

```yaml
# Messages.app knowledge pack — Phase 1
app: Messages
tier: 1
bundle_ids:
  - com.apple.MobileSMS
aliases:
  - messages
  - imessage
shortcuts:
  - "⌘N — new message"
  - "⌘F — search messages"
  - "↩ — send the typed message"
  - "⌘↑/⌘↓ — move between conversations"
recipes:
  send_message:
    - "Prefer AppleScript: tell application \"Messages\" to send \"<text>\" to buddy \"<name>\""
    - "Or ⌘N, type the recipient, Tab to the body, type the message, press Return"
  search_conversation:
    - "⌘F, type the contact or keyword, Return"
gotchas:
  - "Sending requires the recipient to be a known iMessage/SMS contact"
  - "Group chats may not be addressable by a single buddy name via AppleScript"
  - "Automation prompts for permission the first time AppleScript drives Messages"
scripting:
  run_applescript: "Drive Messages via AppleScript for deterministic sending"
```

- [ ] **Step 2: Create `facetime.yaml`**

```yaml
# FaceTime.app knowledge pack — Phase 1
app: FaceTime
tier: 2
bundle_ids:
  - com.apple.FaceTime
aliases:
  - facetime
shortcuts:
  - "⌘N — new FaceTime"
  - "⌘L — toggle the left sidebar"
  - "⌘W — close window / end call view"
recipes:
  start_call:
    - "⌘N, type the contact name or number, then click the FaceTime/Audio button"
    - "Or use the facetime:// URL scheme via safari_open_url, e.g. facetime://<number>"
  join_link:
    - "Open the FaceTime web/link with safari_open_url, then click Join"
gotchas:
  - "FaceTime has very limited AppleScript support — prefer AX clicks + URL scheme"
  - "Starting a call may show a confirmation; element labels vary by macOS version"
```

- [ ] **Step 3: Create `contacts.yaml`**

```yaml
# Contacts.app knowledge pack — Phase 1
app: Contacts
tier: 1
bundle_ids:
  - com.apple.AddressBook
aliases:
  - contacts
  - address book
shortcuts:
  - "⌘N — new contact"
  - "⌘F — search contacts"
  - "⌘E — edit selected contact"
  - "⌘S — save edits"
recipes:
  find_contact:
    - "⌘F, type the name, Return; the matching card shows phone/email"
    - "Or AppleScript: tell application \"Contacts\" to get the value of every phone of person \"<name>\""
  new_contact:
    - "⌘N, fill name/phone/email fields, ⌘S to save"
gotchas:
  - "Editing requires clicking Edit (⌘E) before fields are writable"
  - "Duplicate names need disambiguation by company or email"
scripting:
  run_applescript: "Query/modify contacts deterministically via AppleScript"
```

- [ ] **Step 4: Create `reminders.yaml`**

```yaml
# Reminders.app knowledge pack — Phase 1
app: Reminders
tier: 1
bundle_ids:
  - com.apple.reminders
aliases:
  - reminders
shortcuts:
  - "⌘N — new reminder"
  - "⌘F — search"
  - "⌘1/⌘2/⌘3 — switch between Today/Scheduled/All"
recipes:
  add_reminder:
    - "AppleScript: tell application \"Reminders\" to make new reminder with properties {name:\"<text>\"}"
    - "Or ⌘N, type the reminder text, set a date with natural language, Return"
  complete_reminder:
    - "Click the radio circle to the left of the reminder, or select it and press the spacebar"
gotchas:
  - "A specific list must exist before adding to it via AppleScript"
  - "Natural-language dates ('tomorrow 9am') are parsed only in the typed UI, not AppleScript"
scripting:
  run_applescript: "Create/complete reminders deterministically via AppleScript"
```

- [ ] **Step 5: Validate the packs**

Run: `make validate-packs`
Expected: `OK: N built-in packs validated` (N now includes the 4 new packs).

- [ ] **Step 6: Confirm resolution + commit**

Run: `python -c "from aether.knowledge import loader; print(loader.resolve_pack_key(bundle_id='com.apple.reminders'), loader.resolve_pack_key('Messages'))"`
Expected: `reminders reminders`... actually prints `reminders messages`.

```bash
git add aether/knowledge/packs/messages.yaml aether/knowledge/packs/facetime.yaml aether/knowledge/packs/contacts.yaml aether/knowledge/packs/reminders.yaml
git commit -m "feat(packs): add Messages, FaceTime, Contacts, Reminders"
```

---

## Task C3: Apple iWork packs (Keynote, Pages, Numbers)

**Files:**
- Create: `aether/knowledge/packs/keynote.yaml`, `pages.yaml`, `numbers.yaml`

- [ ] **Step 1: Create `keynote.yaml`**

```yaml
# Keynote.app knowledge pack — Phase 1
app: Keynote
tier: 1
bundle_ids:
  - com.apple.iWork.Keynote
aliases:
  - keynote
shortcuts:
  - "⌘N — new presentation"
  - "⌥⌘P — play slideshow"
  - "⇧⌘N — new slide"
  - "⌘= — zoom in"
recipes:
  new_slide:
    - "⇧⌘N for a new slide, or AppleScript: tell application \"Keynote\" to make new slide at end of slides of front document"
  start_presentation:
    - "⌥⌘P to play; Esc to exit"
  export_pdf:
    - "File ▸ Export To ▸ PDF…, or AppleScript export with format PDF"
gotchas:
  - "Editing text requires double-clicking into a text box first"
  - "Theme/template selection appears only on new-document creation"
scripting:
  run_applescript: "Keynote has rich AppleScript for slides/export"
```

- [ ] **Step 2: Create `pages.yaml`**

```yaml
# Pages.app knowledge pack — Phase 1
app: Pages
tier: 1
bundle_ids:
  - com.apple.iWork.Pages
aliases:
  - pages
shortcuts:
  - "⌘N — new document"
  - "⌘B/⌘I/⌘U — bold/italic/underline"
  - "⇧⌘T — toggle toolbar/inspector"
  - "⌘P — print/export"
recipes:
  insert_text:
    - "Click into the body, type; or AppleScript: tell application \"Pages\" to set body text of front document to \"<text>\""
  export_pdf:
    - "File ▸ Export To ▸ PDF…, or AppleScript export with format PDF"
gotchas:
  - "Page-layout vs word-processing documents behave differently for text insertion"
  - "Template chooser only appears for new documents"
scripting:
  run_applescript: "Pages supports AppleScript for text/export"
```

- [ ] **Step 3: Create `numbers.yaml`**

```yaml
# Numbers.app knowledge pack — Phase 1
app: Numbers
tier: 1
bundle_ids:
  - com.apple.iWork.Numbers
aliases:
  - numbers
shortcuts:
  - "⌘N — new spreadsheet"
  - "↩ — confirm cell entry"
  - "⌘= — autosum (with cells selected)"
  - "⌥↩ — newline within a cell"
recipes:
  set_cell:
    - "Click the cell, type the value, press Return"
    - "AppleScript: tell application \"Numbers\" to set value of cell \"B2\" of table 1 of sheet 1 of front document to \"<v>\""
  export_csv:
    - "File ▸ Export To ▸ CSV…, or AppleScript export with format CSV"
gotchas:
  - "Cell references are sheet/table-scoped; the first table is usually 'Table 1'"
  - "Formulas must start with '=' typed into the cell"
scripting:
  run_applescript: "Numbers supports AppleScript for cells/export"
```

- [ ] **Step 4: Validate + commit**

Run: `make validate-packs`
Expected: `OK: N built-in packs validated`.

```bash
git add aether/knowledge/packs/keynote.yaml aether/knowledge/packs/pages.yaml aether/knowledge/packs/numbers.yaml
git commit -m "feat(packs): add Keynote, Pages, Numbers"
```

---

## Task C4: Apple media/system packs (Photos, Music, Preview, System Settings, Maps)

**Files:**
- Create: `aether/knowledge/packs/photos.yaml`, `music.yaml`, `preview.yaml`, `system_settings.yaml`, `maps.yaml`

- [ ] **Step 1: Create `photos.yaml`**

```yaml
# Photos.app knowledge pack — Phase 1
app: Photos
tier: 2
bundle_ids:
  - com.apple.Photos
aliases:
  - photos
shortcuts:
  - "⌘F — search photos"
  - "↩ — open selected photo"
  - "⌘\\ — toggle sidebar"
  - "⌘. — exit full-screen/edit"
recipes:
  search_photos:
    - "⌘F, type a place/person/thing keyword, Return"
  edit_photo:
    - "Select a photo, press Return to open, click Edit (top-right)"
gotchas:
  - "Photos has limited AppleScript — prefer AX clicks and keyboard"
  - "Editing controls are only visible after entering Edit mode"
```

- [ ] **Step 2: Create `music.yaml`**

```yaml
# Music.app knowledge pack — Phase 1
app: Music
tier: 1
bundle_ids:
  - com.apple.Music
aliases:
  - music
  - apple music
shortcuts:
  - "Space — play/pause"
  - "⌘F — search"
  - "⌘→/⌘← — next/previous track"
  - "⌘↑/⌘↓ — volume up/down"
recipes:
  play_song:
    - "AppleScript: tell application \"Music\" to play (first track whose name is \"<song>\")"
    - "Or ⌘F, type the song, Return, then press Return on the result"
  pause:
    - "Press Space, or AppleScript: tell application \"Music\" to pause"
gotchas:
  - "Search spans Apple Music catalog and library; library-only playback needs an owned track"
  - "AppleScript controls the desktop app, not AirPlay targets"
scripting:
  run_applescript: "Music supports AppleScript for playback control"
```

- [ ] **Step 3: Create `preview.yaml`**

```yaml
# Preview.app knowledge pack — Phase 1
app: Preview
tier: 2
bundle_ids:
  - com.apple.Preview
aliases:
  - preview
shortcuts:
  - "⌘F — search within the PDF"
  - "⌘+ / ⌘- — zoom in/out"
  - "⇧⌘A — show/hide markup toolbar"
  - "⌘P — print"
recipes:
  search_pdf:
    - "⌘F, type the query, Return; ↩ cycles matches"
  annotate:
    - "⇧⌘A to show markup, choose a tool, then draw/type on the page"
  export:
    - "File ▸ Export…, choose a format; for PDF use File ▸ Export as PDF"
gotchas:
  - "Markup tools are hidden until the markup toolbar is shown (⇧⌘A)"
  - "Multi-page navigation uses the sidebar thumbnails (⌥⌘2)"
```

- [ ] **Step 4: Create `system_settings.yaml`**

```yaml
# System Settings.app knowledge pack — Phase 1
app: System Settings
tier: 2
bundle_ids:
  - com.apple.systempreferences
aliases:
  - system settings
  - system preferences
  - settings
shortcuts:
  - "⌘F — search all settings"
  - "⌘[ / ⌘] — back / forward between panes"
recipes:
  open_pane:
    - "Prefer URL scheme via safari_open_url, e.g. x-apple.systempreferences:com.apple.preference.network"
    - "Or ⌘F, type the setting name (e.g. 'Wi-Fi'), Return"
  search_setting:
    - "⌘F, type the keyword, pick the highlighted result"
gotchas:
  - "Pane identifiers (x-apple.systempreferences:...) changed in macOS Ventura+"
  - "The settings list is a scrolling sidebar; use search rather than scrolling"
```

- [ ] **Step 5: Create `maps.yaml`**

```yaml
# Maps.app knowledge pack — Phase 1
app: Maps
tier: 2
bundle_ids:
  - com.apple.Maps
aliases:
  - maps
shortcuts:
  - "⌘F — search for a place"
  - "⌘L — show your location"
  - "⌘+ / ⌘- — zoom in/out"
recipes:
  search_place:
    - "Prefer URL scheme via safari_open_url, e.g. maps://?q=<place>"
    - "Or ⌘F, type the place, Return"
  get_directions:
    - "maps://?saddr=<from>&daddr=<to>, or click Directions after selecting a place"
gotchas:
  - "Driving/walking mode is a toggle in the directions panel"
  - "URL-scheme queries are the most deterministic way to drive Maps"
```

- [ ] **Step 6: Validate + commit**

Run: `make validate-packs`
Expected: `OK: N built-in packs validated`.

```bash
git add aether/knowledge/packs/photos.yaml aether/knowledge/packs/music.yaml aether/knowledge/packs/preview.yaml aether/knowledge/packs/system_settings.yaml aether/knowledge/packs/maps.yaml
git commit -m "feat(packs): add Photos, Music, Preview, System Settings, Maps"
```

---

## Task C5: Third-party packs (Arc, Discord, Obsidian, Linear)

**Files:**
- Create: `aether/knowledge/packs/arc.yaml`, `discord.yaml`, `obsidian.yaml`, `linear.yaml`

- [ ] **Step 1: Create `arc.yaml`**

```yaml
# Arc browser knowledge pack — Phase 1
app: Arc
tier: 2
bundle_ids:
  - company.thebrowser.Browser
aliases:
  - arc
shortcuts:
  - "⌘T — new tab"
  - "⌘L — focus the URL/command bar"
  - "⌘S — toggle the sidebar"
  - "⌃⌘↑/↓ — switch spaces / move tabs"
recipes:
  open_url:
    - "⌘L, type or paste the URL, Return"
  new_tab:
    - "⌘T, type the query/URL, Return"
  search_tabs:
    - "⌘L opens the unified command bar; type to filter open tabs"
gotchas:
  - "Arc is Chromium-based; many Chrome shortcuts apply but the sidebar/spaces model differs"
  - "Pinned vs today tabs live in different sidebar sections"
```

- [ ] **Step 2: Create `discord.yaml`**

```yaml
# Discord knowledge pack — Phase 1
app: Discord
tier: 2
bundle_ids:
  - com.hnc.Discord
aliases:
  - discord
shortcuts:
  - "⌘K — quick switcher (jump to server/channel/DM)"
  - "↩ — send the typed message"
  - "⇧↩ — newline without sending"
  - "⌘/ — keyboard shortcuts help"
recipes:
  send_message:
    - "⌘K, type the channel or person, Return to jump, then type the message and press Return"
  jump_to_channel:
    - "⌘K, type the channel name, Return"
gotchas:
  - "Discord is Electron; AX labels are generic — prefer ⌘K navigation over clicking"
  - "Markdown in messages: **bold**, *italic*, ``code`` are rendered on send"
```

- [ ] **Step 3: Create `obsidian.yaml`**

```yaml
# Obsidian knowledge pack — Phase 1
app: Obsidian
tier: 2
bundle_ids:
  - md.obsidian
aliases:
  - obsidian
shortcuts:
  - "⌘O — quick open a note"
  - "⌘P — command palette"
  - "⌘N — new note"
  - "⌘E — toggle edit/preview"
recipes:
  open_note:
    - "⌘O, type the note title, Return"
    - "Or use the obsidian:// URI via safari_open_url, e.g. obsidian://open?vault=<vault>&file=<note>"
  new_note:
    - "⌘N, type the title, then the body"
  run_command:
    - "⌘P, type the command name (e.g. 'Insert template'), Return"
gotchas:
  - "Obsidian is Electron; most actions are reachable via the ⌘P command palette"
  - "obsidian:// URIs require the vault name to be known"
```

- [ ] **Step 4: Create `linear.yaml`**

```yaml
# Linear knowledge pack — Phase 1
app: Linear
tier: 2
bundle_ids:
  - com.linear.linear
aliases:
  - linear
shortcuts:
  - "⌘K — command menu"
  - "C — create a new issue"
  - "/ — focus search"
  - "G then I — go to My Issues"
recipes:
  create_issue:
    - "Press C, type the title, Tab to description, then ⌘↩ to submit"
  search_issue:
    - "Press / or ⌘K, type the query, Return"
  go_to_view:
    - "⌘K, type the view/team name (e.g. 'Backlog'), Return"
gotchas:
  - "Linear is keyboard-first; single-letter shortcuts (C, /) only work when no field is focused"
  - "The desktop app wraps the web app; AX labels are generic — prefer ⌘K"
```

- [ ] **Step 5: Validate + confirm resolution + commit**

Run: `make validate-packs`
Expected: `OK: N built-in packs validated`.

Run: `python -c "from aether.knowledge import loader; print(loader.resolve_pack_key(bundle_id='md.obsidian'), loader.resolve_pack_key('Arc'))"`
Expected: `obsidian arc`.

```bash
git add aether/knowledge/packs/arc.yaml aether/knowledge/packs/discord.yaml aether/knowledge/packs/obsidian.yaml aether/knowledge/packs/linear.yaml
git commit -m "feat(packs): add Arc, Discord, Obsidian, Linear"
```

---

## Task C6: Inventory bump + full-suite verification

**Files:**
- Modify: `README.md`, `docs/ROADMAP.md` (pack count references)
- Test: full suite

- [ ] **Step 1: Update the pack count in docs**

In `README.md` and `docs/ROADMAP.md`, update any "17 packs" / "(17 packs total)" references to **33**. Find them:

Run: `grep -rn "17 pack\|12 app\|17 app" README.md docs/ROADMAP.md`
Then edit each match to read `33 packs` (adjust surrounding wording to match).

In `docs/ROADMAP.md` §3.7, update the inventory note to reflect the added Apple-stack + third-party packs.

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (all unit + integration + security tests green; new Phase 1 tests included).

- [ ] **Step 3: Lint + compile + pack validation**

Run: `ruff check aether sidecar tests && python -m compileall -q aether sidecar tests && make validate-packs`
Expected: no errors; `OK: 33 built-in packs validated`.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/ROADMAP.md
git commit -m "docs: update knowledge-pack inventory to 33"
```

---

## Final Verification Checklist

- [ ] `python -m pytest tests/ -q` — all green.
- [ ] `ruff check aether sidecar tests` — clean.
- [ ] `python -m compileall -q aether sidecar tests` — clean.
- [ ] `make validate-packs` — 33 packs validated.
- [ ] Manual: start sidecar, run a goal, open `/dashboard`, confirm the "Cost & tokens (estimated)" panel populates after a cloud step.
- [ ] Manual (optional): set `roles.local_fast.native_tools: true` with Ollama running a tool-capable model; confirm a tool call is parsed natively (and that flipping it back to `false` still works via JSON-in-text).

---

## Known Limitations (documented, out of scope)

- The auxiliary `VisionLLM.analyze_screenshot` VLM-description call is not token-accounted (it returns a string and `analyze_image` surfaces no usage). Vision-tier *reasoning* steps ARE accounted (they go through `client.step`).
- Cost figures are estimates: provider token counts can differ from billed amounts; Anthropic prompt-cache tiers and failover-retry tokens (tokens spent on a failed provider before failover) are not captured.
- Pricing in `PRICE_TABLE` is static and will drift from real provider prices.
- Pack bundle ids in YAML must be verified per build machine (`osascript -e 'id of app "<Name>"'`).
