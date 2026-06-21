"""HTTP sidecar for the native Aether shell (Phase 4).

Endpoints:
  GET  /health          liveness
  GET  /status          current run + world snapshot
  GET  /metrics         JSON observability snapshot
  GET  /dashboard       local HTML metrics dashboard
  GET  /tools/schemas   exported tool contracts
  POST /run             start agent (JSON body; optional SSE stream)
  POST /stop            global STOP
  POST /stt             transcribe base64 WAV (Groq Whisper or OpenAI)
  POST /tts             synthesize speech (Groq Orpheus TTS)
  POST /tts/stream      chunked/streaming TTS (Phase 10)
  GET  /config/voice    voice engine settings from config.yaml
  GET  /config/mcp      sanitized MCP server list (Phase 10)
  POST /config/mcp/reload  reload MCP sessions (Phase 10)
  GET  /skills          list learned skills (Phase 11)
  GET  /skills/{id}     skill detail (Phase 11)
  POST /skills/{id}/replay  parameterized skill replay (Phase 11)
  POST /metrics/stop    STOP latency from Swift (Phase 12)
  POST /crash-report    opt-in crash reports (Phase 12)
  WS   /voice/realtime  OpenAI Realtime API bridge (Phase 10 beta)
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from aether.core.config import load_config
from aether.core import stop as stop_ctl
from aether.core.orchestrator import Agent
from aether.core.metrics import MetricsCollector
from aether.core.audit_log import AuditLog
from aether.voice.stt import STT
from aether.voice.tts import TTS
from aether.tools.mcp_client import MCPClient, get_active_mcp_client, set_active_mcp_client

from .auth import cors_origins, require_auth
from .errors import redact_error_message, structured_error
from .hud_bridge import StreamingHUD, patch_agent_for_sidecar
from . import confirmation
from . import percept_store
from .rate_limit import get_limiter

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
_START_TIME = time.time()


def _read_version() -> str:
    for candidate in (ROOT / "VERSION", ROOT / "aether" / "__init__.py"):
        if candidate.name == "VERSION" and candidate.exists():
            return candidate.read_text(encoding="utf-8").strip()
        if candidate.name == "__init__.py" and candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                if line.startswith("__version__"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.5.0-beta.1"


_VERSION = _read_version()


app = FastAPI(title="Aether Sidecar", version=_VERSION)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limit(request: Request, bucket: str) -> None:
    ok, retry_after = get_limiter().allow(bucket, _client_key(request))
    if not ok:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded for {bucket}. Retry after {retry_after:.1f}s.",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=structured_error(
            code=f"http_{exc.status_code}",
            message=str(exc.detail),
            status_code=exc.status_code,
        ),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled sidecar error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content=structured_error(
            code="internal_error",
            message=redact_error_message(str(exc) or type(exc).__name__),
            status_code=500,
        ),
    )


class RunRequest(BaseModel):
    goal: str
    careful: bool = False
    local_only: bool = False
    narrate: bool = False
    stream: bool = True
    max_steps: int | None = None


class STTRequest(BaseModel):
    audio_base64: str = Field(..., description="WAV audio, base64-encoded")
    engine: str | None = None


class TTSRequest(BaseModel):
    text: str
    engine: str | None = None


class ConfirmRequest(BaseModel):
    request_id: str
    approved: bool


class VoiceMetricsRequest(BaseModel):
    stt_ms: float | None = None
    tts_ms: float | None = None
    voice_rtt_ms: float | None = None
    run_id: str | None = None


class ScreenPerceptRequest(BaseModel):
    width: int = 0
    height: int = 0
    fps: float = 0.0
    frontmost_app: str = ""
    note: str = ""
    frame_hash: str | None = None


class FeedbackRequest(BaseModel):
    message: str
    category: str = "general"
    email: str | None = None
    app_version: str | None = None


class StopMetricsRequest(BaseModel):
    stop_latency_ms: float
    source: str = "swift"


class CrashReportRequest(BaseModel):
    message: str
    stack: str | None = None
    app_version: str | None = None
    fatal: bool = False


class SkillReplayRequest(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)
    via_orchestrator: bool = True
    goal_override: str | None = None


@dataclass
class RunState:
    run_id: str
    goal: str
    status: str = "running"  # running | idle | stopped | error
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: str | None = None
    error: str | None = None
    last_hud: dict[str, Any] = field(default_factory=dict)
    world_snapshot: dict[str, Any] = field(default_factory=dict)


_run_lock = threading.Lock()
_current: RunState | None = None
_streaming_hud = StreamingHUD()
_event_subscribers: list[asyncio.Queue[dict[str, Any]]] = []
_subscribers_lock = asyncio.Lock()


def _set_current(state: RunState | None) -> None:
    global _current
    with _run_lock:
        _current = state


def _get_current() -> RunState | None:
    with _run_lock:
        return _current


async def _broadcast(event: dict[str, Any]) -> None:
    async with _subscribers_lock:
        dead: list[asyncio.Queue] = []
        for q in _event_subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            if q in _event_subscribers:
                _event_subscribers.remove(q)


async def _run_agent_task(
    run_id: str,
    goal: str,
    *,
    careful: bool,
    local_only: bool,
    narrate: bool,
    max_steps: int | None,
    event_queue: asyncio.Queue[dict[str, Any]],
    loop: asyncio.AbstractEventLoop,
) -> None:
    state = RunState(run_id=run_id, goal=goal)
    _set_current(state)

    cfg = load_config()
    if careful:
        cfg.raw.setdefault("agent", {})["careful"] = True
    if local_only:
        cfg.raw.setdefault("agent", {})["local_only"] = True
    if max_steps is not None:
        cfg.raw.setdefault("agent", {})["max_steps"] = max_steps
    cfg.raw.setdefault("hud", {})["enabled"] = False
    if narrate:
        cfg.raw.setdefault("agent", {})["narrate"] = True

    stream_summary = percept_store.summary_for_context()
    agent = Agent(cfg, hud=_streaming_hud)
    if stream_summary:
        agent.world.set_screen_stream(stream_summary)
    patch_agent_for_sidecar(agent, _streaming_hud, event_queue, loop)
    if not narrate:
        agent.cfg.raw.setdefault("agent", {})["narrate"] = False

    stop_ctl.reset()
    await _broadcast({"type": "run_start", "run_id": run_id, "goal": goal})

    async def pump_events() -> None:
        while True:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                if state.status != "running":
                    break
                continue
            if event.get("type") == "hud":
                state.last_hud = {k: v for k, v in event.items() if k != "type"}
                try:
                    state.world_snapshot = agent.world.snapshot()
                except Exception:
                    pass
            await _broadcast(event)
            if event.get("type") in {"done", "error"}:
                break

    pump_task = asyncio.create_task(pump_events())

    try:
        result = await agent.run_async(goal, run_id=run_id)
        state.status = "idle"
        state.result = result
        state.finished_at = time.time()
        try:
            state.world_snapshot = agent.world.snapshot()
        except Exception:
            pass
        done_event = {
            "type": "done",
            "run_id": run_id,
            "result": result,
            "world": state.world_snapshot,
        }
        event_queue.put_nowait(done_event)
        await _broadcast(done_event)
    except Exception as exc:  # noqa: BLE001
        state.status = "error"
        state.error = str(exc)
        state.finished_at = time.time()
        err_event = {"type": "error", "run_id": run_id, "message": str(exc)}
        event_queue.put_nowait(err_event)
        await _broadcast(err_event)
    finally:
        _streaming_hud.unbind()
        pump_task.cancel()
        try:
            await pump_task
        except asyncio.CancelledError:
            pass
        if state.status == "running":
            state.status = "stopped"
            state.finished_at = time.time()


def _skill_store():
    from aether.memory.skills import SkillStore

    cfg = load_config(validate=False)
    skills_cfg = cfg.get("skills") or {}
    if not skills_cfg.get("enabled", True):
        raise HTTPException(503, "Skills are disabled in config")
    mem_cfg = cfg.get("memory") or {}
    return SkillStore(
        skills_cfg.get("db_path"),
        embedding_provider=str(mem_cfg.get("embedding_provider", "hash")),
        openai_api_key=cfg.openai_api_key,
    )


def _skill_to_dict(skill) -> dict[str, Any]:
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "goal_pattern": skill.goal_pattern,
        "parameters": skill.parameters,
        "steps": skill.steps,
        "success_count": skill.success_count,
        "created_at": skill.created_at,
        "updated_at": skill.updated_at,
    }


@app.get("/skills")
async def list_skills() -> dict[str, Any]:
    """List learned skills (Phase 11, FR-25)."""
    store = _skill_store()
    try:
        skills = store.list_skills()
        return {"skills": [_skill_to_dict(s) for s in skills]}
    finally:
        store.close()


@app.get("/skills/{skill_id}")
async def get_skill(skill_id: int) -> dict[str, Any]:
    store = _skill_store()
    try:
        skill = store.get_skill(skill_id)
        if not skill:
            raise HTTPException(404, f"Skill {skill_id} not found")
        return {"skill": _skill_to_dict(skill)}
    finally:
        store.close()


@app.post("/skills/{skill_id}/replay")
async def replay_skill(
    skill_id: int,
    body: SkillReplayRequest,
    _auth: None = Depends(require_auth),
) -> dict[str, Any]:
    """Replay a skill with substituted parameters (Phase 11, FR-25)."""
    store = _skill_store()
    try:
        skill = store.get_skill(skill_id)
        if not skill:
            raise HTTPException(404, f"Skill {skill_id} not found")

        if body.via_orchestrator:
            cur = _get_current()
            if cur and cur.status == "running":
                raise HTTPException(409, "A run is already in progress")
            goal = body.goal_override or store.build_replay_goal(skill, body.args)
            run_id = uuid.uuid4().hex[:12]
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            loop = asyncio.get_running_loop()
            await _run_agent_task(
                run_id,
                goal,
                careful=False,
                local_only=False,
                narrate=False,
                max_steps=None,
                event_queue=queue,
                loop=loop,
            )
            cur = _get_current()
            return {
                "mode": "orchestrator",
                "run_id": run_id,
                "goal": goal,
                "status": cur.status if cur else "idle",
                "result": cur.result if cur else None,
            }

        from aether.tools.registry import DEFAULT_REGISTRY, AgentContext
        from aether.core.world_model import WorldModel
        from aether.core.policy import Policy, PolicyConfig, normalize_file_roots

        cfg = load_config(validate=False)
        policy_cfg = cfg.get("policy") or {}
        policy = Policy(PolicyConfig(
            careful=cfg.careful,
            capabilities=cfg.get("capabilities"),
            approved_file_roots=normalize_file_roots(
                policy_cfg.get("approved_file_roots")
            ),
            network_allowlist=list(policy_cfg.get("network_allowlist") or []),
        ))
        ctx = AgentContext(
            careful=cfg.careful,
            world=WorldModel(),
            memory=None,
            browser_headless=cfg.browser_headless,
        )

        async def _confirm(tool: str, desc: str, _args: dict[str, Any]) -> bool:
            return await confirmation.request_confirmation(
                desc,
                tool=tool,
            )

        result = await store.replay_async(
            skill_id,
            body.args,
            dispatch=DEFAULT_REGISTRY.dispatch,
            ctx=ctx,
            policy=policy,
            registry=DEFAULT_REGISTRY,
            confirm=_confirm,
        )
        return {
            "mode": "direct",
            "skill_id": result.skill_id,
            "success": result.success,
            "steps_executed": result.steps_executed,
            "observations": result.observations,
            "error": result.error,
        }
    finally:
        store.close()


@app.get("/health")
async def health() -> dict[str, Any]:
    from aether.perception import accessibility as ax

    cfg = load_config()
    audit_cfg = cfg.get("audit") or {}
    audit = AuditLog.get(
        path=audit_cfg.get("path"),
        enabled=bool(audit_cfg.get("enabled", True)),
    )
    chain_ok, chain_msg = audit.verify_chain(max_records=50)
    return {
        "ok": True,
        "service": "aether-sidecar",
        "version": _VERSION,
        "uptime_sec": round(time.time() - _START_TIME, 1),
        "timestamp": time.time(),
        "permissions": {
            "accessibility_pyobjc": ax.available(),
            "cloud_llm": cfg.has_cloud_llm(),
            "anthropic_api_key": bool(cfg.anthropic_api_key),
            "openai_api_key": bool(cfg.openai_api_key),
            "configured_providers": {
                k: bool(v) for k, v in (cfg.api_keys or {}).items() if v
            },
        },
        "audit": {
            "enabled": audit.enabled,
            "path": str(audit.path),
            "key_source": audit.key_source,
            "chain_ok": chain_ok,
            "chain_status": chain_msg,
        },
        "beta_flags": cfg.get("beta") or {},
    }


@app.get("/audit/verify")
async def audit_verify() -> dict[str, Any]:
    cfg = load_config()
    audit_cfg = cfg.get("audit") or {}
    audit = AuditLog.get(
        path=audit_cfg.get("path"),
        enabled=bool(audit_cfg.get("enabled", True)),
    )
    ok, msg = audit.verify_chain()
    return {"ok": ok, "message": msg, "path": str(audit.path)}


@app.get("/metrics")
async def metrics(_auth: None = Depends(require_auth)) -> dict[str, Any]:
    return MetricsCollector.get().snapshot()


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(_auth: None = Depends(require_auth)) -> str:
    snap = MetricsCollector.get().snapshot()
    counters = snap.get("counters", {})
    histograms = snap.get("histograms", {})
    runs = snap.get("recent_runs", [])
    rows = "".join(
        f"<tr><td>{r.get('goal','')}</td><td>{r.get('status')}</td>"
        f"<td>{r.get('steps')}</td><td>{r.get('tool_calls')}</td>"
        f"<td>{r.get('duration_ms')}</td></tr>"
        for r in runs
    )
    hist_rows = "".join(
        f"<tr><td>{name}</td><td>{h.get('count')}</td>"
        f"<td>{h.get('p50')}</td><td>{h.get('p95')}</td></tr>"
        for name, h in histograms.items()
    )
    counter_rows = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in sorted(counters.items())
    )
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
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Aether Metrics</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 24px; background: #0d1117; color: #e6edf3; }}
h1 {{ font-size: 1.25rem; }}
table {{ border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 13px; }}
th, td {{ border: 1px solid #30363d; padding: 8px; text-align: left; }}
th {{ background: #161b22; }}
.meta {{ color: #8b949e; font-size: 12px; }}
</style></head><body>
<h1>Aether Observability</h1>
<p class="meta">Local-only dashboard — 127.0.0.1 · refresh to update</p>
<h2>Counters</h2><table><tr><th>Metric</th><th>Value</th></tr>{counter_rows}</table>
<h2>Latency histograms (ms)</h2><table><tr><th>Name</th><th>Count</th><th>p50</th><th>p95</th></tr>{hist_rows}</table>
<h2>Cost &amp; tokens (estimated)</h2>
<p class="meta">Estimates from provider token counts — not billing-accurate.</p>
<table><tr><th>Provider</th><th>Tokens in</th><th>Tokens out</th><th>Calls</th><th>Cost USD</th></tr>{cost_rows}</table>
<h2>Recent runs</h2><table><tr><th>Goal</th><th>Status</th><th>Steps</th><th>Tools</th><th>Duration ms</th></tr>{rows}</table>
</body></html>"""


@app.get("/status")
async def status() -> dict[str, Any]:
    cur = _get_current()
    if cur is None:
        return {"status": "idle", "run": None}
    return {
        "status": cur.status,
        "run": {
            "id": cur.run_id,
            "goal": cur.goal,
            "started_at": cur.started_at,
            "finished_at": cur.finished_at,
            "result": cur.result,
            "error": cur.error,
            "hud": cur.last_hud,
            "world": cur.world_snapshot,
        },
    }


@app.get("/tools/schemas")
async def tool_schemas() -> JSONResponse:
    manifest = ROOT / "shared" / "tool_schemas" / "manifest.json"
    if not manifest.exists():
        raise HTTPException(404, "Tool schemas not exported. Run scripts/export_tool_schemas.py")
    return JSONResponse(content=json.loads(manifest.read_text()))


@app.get("/tools/schemas/{name}")
async def tool_schema(name: str) -> JSONResponse:
    path = ROOT / "shared" / "tool_schemas" / f"{name}.json"
    if not path.exists():
        raise HTTPException(404, f"Unknown tool: {name}")
    return JSONResponse(content=json.loads(path.read_text()))


@app.post("/stop")
async def stop(request: Request, _auth: None = Depends(require_auth)) -> dict[str, Any]:
    started = time.perf_counter()
    stop_ctl.trigger("sidecar")
    cur = _get_current()
    if cur and cur.status == "running":
        cur.status = "stopped"
        cur.finished_at = time.time()
        cur.result = "Stopped by user."
    await _broadcast({"type": "stopped", "reason": "user"})
    latency_ms = (time.perf_counter() - started) * 1000
    m = MetricsCollector.get()
    m.observe("stop_latency_ms", latency_ms)
    m.warn_if_slow("stop_latency_ms", latency_ms)
    m.inc("stop_requests")
    return {"status": "stopped", "stop_latency_ms": round(latency_ms, 2)}


@app.get("/config/voice")
async def voice_config() -> dict[str, Any]:
    cfg = load_config()
    beta = cfg.get("beta") or {}
    return {
        "stt": cfg.stt,
        "stt_model": cfg.stt_model,
        "tts": cfg.tts,
        "tts_model": cfg.tts_model,
        "tts_voice": cfg.tts_voice,
        "tts_stream": bool(cfg.get("voice", "tts_stream", default=True)),
        "mode": str(cfg.get("voice", "mode", default="pipeline")),
        "realtime_provider": str(cfg.get("voice", "realtime_provider", default="openai")),
        "realtime_voice": bool(beta.get("realtime_voice", False)),
        "barge_in": bool(cfg.get("voice", "barge_in", default=True)),
        "vad_energy_threshold": float(cfg.get("voice", "vad_energy_threshold", default=0.02)),
    }


@app.get("/config/mcp")
async def mcp_config() -> dict[str, Any]:
    """Sanitized MCP server list for Swift settings UI (Phase 10)."""
    cfg = load_config()
    mcp_cfg = {
        **(cfg.get("mcp") or {}),
        "careful": cfg.careful,
    }
    active = get_active_mcp_client()
    if active is not None:
        servers = active.server_status()
    else:
        client = MCPClient(mcp_cfg)
        servers = client.server_status()
    return {
        "enabled": bool(mcp_cfg.get("enabled", False)),
        "servers": servers,
        "reload_note": "POST /config/mcp/reload closes sessions; new tools load on next agent run.",
    }


@app.post("/config/mcp/reload")
async def mcp_reload(_auth: None = Depends(require_auth)) -> dict[str, Any]:
    """Reload MCP sessions without full sidecar restart (Phase 10)."""
    cfg = load_config()
    active = get_active_mcp_client()
    if active is not None:
        result = active.reload()
    else:
        client = MCPClient({
            **(cfg.get("mcp") or {}),
            "careful": cfg.careful,
        })
        set_active_mcp_client(client)
        result = client.reload()
    return result


@app.get("/config/beta")
async def beta_config() -> dict[str, Any]:
    cfg = load_config()
    beta = cfg.get("beta") or {}
    return {
        "continuous_screen_stream": bool(beta.get("continuous_screen_stream", False)),
        "ambient_listening": bool(beta.get("ambient_listening", False)),
        "wake_word": bool(beta.get("wake_word", False)),
        "wake_word_engine": str(beta.get("wake_word_engine", "energy")),
        "screen_stream_fps": float(beta.get("screen_stream_fps", 0.5)),
        "native_effectors": bool(beta.get("native_effectors", False)),
        "realtime_voice": bool(beta.get("realtime_voice", False)),
        "auto_update_check": bool(beta.get("auto_update_check", True)),
        "update_feed_url": str(beta.get("update_feed_url", "")),
        "sparkle_appcast_url": str(beta.get("sparkle_appcast_url", "")),
        "crash_reporting": bool(beta.get("crash_reporting", False)),
        "plugins_enabled": bool(beta.get("plugins_enabled", False)),
    }


@app.post("/stt")
async def stt(body: STTRequest, _auth: None = Depends(require_auth)) -> dict[str, str]:
    metrics = MetricsCollector.get()
    t0 = time.perf_counter()
    cfg = load_config()
    engine = body.engine or cfg.stt
    stt_engine = STT(
        engine=engine,
        model=cfg.stt_model,
        openai_api_key=cfg.openai_api_key,
        groq_api_key=cfg.groq_api_key,
    )
    if engine == "none":
        raise HTTPException(501, "STT disabled (voice.stt=none).")
    if engine == "groq" and not cfg.groq_api_key:
        raise HTTPException(
            501,
            "STT unavailable: set voice.stt=groq and GROQ_API_KEY in .env.",
        )
    if engine == "openai" and not cfg.openai_api_key:
        raise HTTPException(
            501,
            "STT unavailable: set voice.stt=openai and OPENAI_API_KEY in .env.",
        )
    try:
        raw = base64.b64decode(body.audio_base64)
    except Exception as exc:
        raise HTTPException(400, f"Invalid base64 audio: {exc}") from exc

    path = Path(tempfile.gettempdir()) / f"aether-stt-{uuid.uuid4().hex}.wav"
    path.write_bytes(raw)
    try:
        text = stt_engine.transcribe_file(path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"STT failed: {exc}") from exc
    finally:
        path.unlink(missing_ok=True)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    metrics.observe("stt_ms", elapsed_ms)
    metrics.inc("voice_stt_requests")
    log.info("voice /stt completed in %.0f ms", elapsed_ms)
    return {"text": text}


@app.post("/tts")
async def tts(body: TTSRequest, _auth: None = Depends(require_auth)) -> StreamingResponse:
    metrics = MetricsCollector.get()
    t0 = time.perf_counter()
    cfg = load_config()
    engine = body.engine or cfg.tts
    tts_engine = TTS(
        engine=engine,
        voice=cfg.tts_voice,
        model=cfg.tts_model,
        groq_api_key=cfg.groq_api_key,
    )
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    if engine == "none":
        raise HTTPException(501, "TTS disabled (voice.tts=none).")
    if engine == "groq" and not cfg.groq_api_key:
        raise HTTPException(
            501,
            "TTS unavailable: set voice.tts=groq and GROQ_API_KEY in .env.",
        )
    if engine not in {"groq"}:
        raise HTTPException(
            501,
            f"TTS engine {engine!r} is not available via sidecar (use macOS AVSpeech locally).",
        )
    try:
        audio = tts_engine.synthesize_bytes(text)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"TTS failed: {exc}") from exc
    elapsed_ms = (time.perf_counter() - t0) * 1000
    metrics.observe("tts_ms", elapsed_ms)
    metrics.inc("voice_tts_requests")
    log.info("voice /tts completed in %.0f ms", elapsed_ms)
    return StreamingResponse(iter([audio]), media_type="audio/wav")


@app.post("/tts/stream")
async def tts_stream(body: TTSRequest, _auth: None = Depends(require_auth)) -> StreamingResponse:
    """Chunked TTS stream for lower-latency Swift playback (Phase 10, FR-21)."""
    metrics = MetricsCollector.get()
    t0 = time.perf_counter()
    cfg = load_config()
    engine = body.engine or cfg.tts
    tts_engine = TTS(
        engine=engine,
        voice=cfg.tts_voice,
        model=cfg.tts_model,
        groq_api_key=cfg.groq_api_key,
    )
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    if engine == "none":
        raise HTTPException(501, "TTS disabled (voice.tts=none).")
    if engine == "groq" and not cfg.groq_api_key:
        raise HTTPException(
            501,
            "TTS unavailable: set voice.tts=groq and GROQ_API_KEY in .env.",
        )
    if engine not in {"groq"}:
        raise HTTPException(
            501,
            f"TTS engine {engine!r} streaming not available via sidecar.",
        )

    def _chunks():
        try:
            for chunk in tts_engine.synthesize_stream(text):
                yield chunk
        finally:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            metrics.observe("tts_stream_ms", elapsed_ms)
            metrics.inc("voice_tts_stream_requests")
            log.info("voice /tts/stream completed in %.0f ms", elapsed_ms)

    return StreamingResponse(_chunks(), media_type="audio/wav")


@app.websocket("/voice/realtime")
async def voice_realtime_bridge(websocket: WebSocket) -> None:
    """WebSocket bridge to OpenAI Realtime API (Phase 10 beta)."""
    from .auth import ws_auth_ok

    if not ws_auth_ok(websocket.headers.get("authorization")):
        await websocket.close(code=4401, reason="Unauthorized")
        return

    cfg = load_config()
    beta = cfg.get("beta") or {}
    if not bool(beta.get("realtime_voice", False)):
        await websocket.close(code=4403, reason="realtime_voice beta flag disabled")
        return
    if str(cfg.get("voice", "mode", default="pipeline")) != "realtime":
        await websocket.close(code=4403, reason="voice.mode is not realtime")
        return

    from aether.voice.realtime import RealtimeConfig, RealtimeSession

    rt_cfg = RealtimeConfig.from_env(
        voice=str(cfg.get("voice", "tts_voice", default="alloy")),
    )
    if rt_cfg is None:
        await websocket.close(code=4401, reason="OPENAI_API_KEY not configured")
        return

    await websocket.accept()
    session = RealtimeSession(config=rt_cfg)
    metrics = MetricsCollector.get()
    session_t0 = time.perf_counter()
    metrics.inc("voice_realtime_sessions")

    async def _forward_to_client(event: dict[str, Any]) -> None:
        try:
            await websocket.send_json(event)
        except Exception:  # noqa: BLE001
            pass

    session.on_event(lambda ev: asyncio.create_task(_forward_to_client(ev)))

    try:
        await session.connect()
        await websocket.send_json({"type": "session.ready", "model": rt_cfg.model})
        while True:
            msg = await websocket.receive_json()
            mtype = msg.get("type", "")
            if mtype == "input_audio":
                import base64

                raw = base64.b64decode(msg.get("audio", ""))
                await session.send_audio_chunk(raw)
            elif mtype == "input_audio.commit":
                await session.commit_audio()
            elif mtype == "input_text":
                await session.send_text(str(msg.get("text", "")))
            elif mtype == "close":
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:  # noqa: BLE001
            pass
    finally:
        session_ms = (time.perf_counter() - session_t0) * 1000
        metrics.observe("voice_realtime_session_ms", session_ms)
        log.info("voice /voice/realtime session ended after %.0f ms", session_ms)
        await session.close()


@app.post("/confirm")
async def confirm_action(
    body: ConfirmRequest,
    _auth: None = Depends(require_auth),
) -> dict[str, Any]:
    ok = confirmation.resolve_confirmation(body.request_id, body.approved)
    if not ok:
        raise HTTPException(404, "Unknown or expired confirmation request")
    return {"status": "ok", "approved": body.approved}


@app.post("/metrics/voice")
async def voice_metrics(
    body: VoiceMetricsRequest,
    _auth: None = Depends(require_auth),
) -> dict[str, str]:
    m = MetricsCollector.get()
    if body.stt_ms is not None:
        m.observe("stt_ms", body.stt_ms)
    if body.tts_ms is not None:
        m.observe("tts_ms", body.tts_ms)
    if body.voice_rtt_ms is not None:
        m.observe("voice_rtt_ms", body.voice_rtt_ms)
        m.inc("voice_roundtrips")
    return {"status": "ok"}


@app.post("/percept/screen")
async def percept_screen(
    body: ScreenPerceptRequest,
    _auth: None = Depends(require_auth),
) -> dict[str, str]:
    percept_store.update(body.model_dump())
    summary = percept_store.summary_for_context()
    cur = _get_current()
    if cur and cur.world_snapshot is not None:
        cur.world_snapshot["screen_stream"] = summary
    return {"status": "ok", "summary": summary}


@app.get("/percept/screen")
async def percept_screen_latest() -> dict[str, Any]:
    return percept_store.latest()


@app.post("/metrics/stop")
async def stop_metrics(
    body: StopMetricsRequest,
    _auth: None = Depends(require_auth),
) -> dict[str, str]:
    m = MetricsCollector.get()
    m.observe("stop_latency_ms", body.stop_latency_ms)
    m.warn_if_slow("stop_latency_ms", body.stop_latency_ms)
    m.inc("stop_client_reports")
    return {"status": "ok"}


@app.post("/crash-report")
async def crash_report(body: CrashReportRequest) -> dict[str, str]:
    cfg = load_config()
    beta = cfg.get("beta") or {}
    if not bool(beta.get("crash_reporting", False)):
        raise HTTPException(503, "Crash reporting is disabled (beta.crash_reporting)")
    store = ROOT / "data" / "crash_reports.jsonl"
    store.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.time(),
        "message": redact_error_message((body.message or "")[:2000]),
        "stack": redact_error_message((body.stack or "")[:8000]),
        "app_version": body.app_version or _VERSION,
        "fatal": body.fatal,
    }
    with store.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    MetricsCollector.get().inc("crash_reports")
    return {"status": "ok"}


@app.post("/feedback")
async def submit_feedback(
    body: FeedbackRequest,
    request: Request,
    _auth: None = Depends(require_auth),
) -> dict[str, str]:
    _check_rate_limit(request, "feedback")
    cfg = load_config()
    fb_cfg = cfg.get("feedback") or {}
    if not bool(fb_cfg.get("enabled", True)):
        raise HTTPException(503, "Feedback collection is disabled")
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(400, "message is required")
    if len(message) > 2000:
        raise HTTPException(400, "message too long (max 2000 characters)")
    category = (body.category or "general")[:64]
    email = (body.email or "")[:254] if body.email else None
    store = ROOT / str(fb_cfg.get("store_path", "data/feedback.jsonl"))
    store.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.time(),
        "category": category,
        "message": message,
        "email": email,
        "app_version": (body.app_version or _VERSION)[:64],
    }
    with store.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    MetricsCollector.get().inc("feedback_submissions")
    return {"status": "ok"}


async def _sse_stream(run_coro) -> AsyncIterator[str]:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
    async with _subscribers_lock:
        _event_subscribers.append(queue)
    try:
        task = asyncio.create_task(run_coro(queue))
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"
                if task.done():
                    break
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") in {"done", "error", "stopped"}:
                break
        await task
    finally:
        async with _subscribers_lock:
            if queue in _event_subscribers:
                _event_subscribers.remove(queue)


@app.post("/run")
async def run_agent(
    body: RunRequest,
    request: Request,
    _auth: None = Depends(require_auth),
):
    _check_rate_limit(request, "run")
    goal = (body.goal or "").strip()
    if not goal:
        raise HTTPException(400, "goal is required")

    cur = _get_current()
    if cur and cur.status == "running":
        raise HTTPException(409, "A run is already in progress")

    cfg = load_config()
    if not cfg.has_cloud_llm() and not body.local_only:
        raise HTTPException(
            400,
            "No cloud LLM API key set. Pass local_only=true or configure .env "
            "(see configs/router.yaml).",
        )

    run_id = uuid.uuid4().hex[:12]
    loop = asyncio.get_running_loop()

    async def run_coro(event_queue: asyncio.Queue[dict[str, Any]]) -> None:
        event_queue.put_nowait({"type": "run_start", "run_id": run_id, "goal": goal})
        await _run_agent_task(
            run_id,
            goal,
            careful=body.careful,
            local_only=body.local_only,
            narrate=body.narrate,
            max_steps=body.max_steps,
            event_queue=event_queue,
            loop=loop,
        )

    if body.stream or "text/event-stream" in request.headers.get("accept", ""):
        return StreamingResponse(_sse_stream(run_coro), media_type="text/event-stream")

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    await _run_agent_task(
        run_id,
        goal,
        careful=body.careful,
        local_only=body.local_only,
        narrate=body.narrate,
        max_steps=body.max_steps,
        event_queue=queue,
        loop=loop,
    )
    cur = _get_current()
    return {
        "run_id": run_id,
        "status": cur.status if cur else "idle",
        "result": cur.result if cur else None,
        "error": cur.error if cur else None,
    }


def main() -> None:
    import uvicorn

    uvicorn.run(
        "sidecar.server:app",
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
