"""Shared pytest fixtures for Aether Phase 6 test foundation."""
from __future__ import annotations

from typing import Any, Generator

import pytest

from aether.core.config import Config
from aether.core import stop as stop_ctl
from aether.core.world_model import WorldModel


@pytest.fixture(autouse=True)
def _reset_stop() -> Generator[None, None, None]:
    """Ensure STOP flag does not leak between tests."""
    stop_ctl.reset()
    yield
    stop_ctl.reset()


@pytest.fixture(autouse=True)
def _reset_mcp_active_client() -> Generator[None, None, None]:
    """Prevent MCP global client from leaking across tests (avoids lock/thread hangs)."""
    from aether.tools import mcp_client as mc

    with mc._mcp_lock:
        mc._active_mcp_client = None
    yield
    with mc._mcp_lock:
        client = mc._active_mcp_client
        mc._active_mcp_client = None
    if client is not None:
        client.close()


@pytest.fixture(autouse=True)
def _reset_metrics() -> Generator[None, None, None]:
    """Reset the MetricsCollector singleton so counters don't leak across tests."""
    from aether.core.metrics import MetricsCollector

    MetricsCollector._instance = None  # noqa: SLF001
    yield
    MetricsCollector._instance = None  # noqa: SLF001


@pytest.fixture
def minimal_config() -> Config:
    """Config without secrets — safe for unit tests."""
    return Config(
        raw={
            "agent": {"max_steps": 3, "careful": False, "local_only": True},
            "router": {"enabled": True, "config_path": "configs/router.yaml"},
            "mcp": {"enabled": False, "servers": []},
            "memory": {"enabled": False},
            "skills": {"enabled": False},
            "audit": {"enabled": False},
            "voice": {"tts": "none", "stt": "none"},
            "hud": {"enabled": False},
        },
        anthropic_api_key=None,
        openai_api_key=None,
        api_keys={},
    )


@pytest.fixture
def world() -> WorldModel:
    """Fresh world model with synthetic AX state (no pyobjc)."""
    w = WorldModel(ax_cache_ttl_ms=0)
    w.set_goal("test goal")
    w.frontmost_app = "Finder"
    w.bundle_id = "com.apple.finder"
    w.elements = [{"idx": 1, "role": "button", "title": "OK"}]
    w.element_count = 5
    w.ax_rendered = "Finder window with OK button"
    w.ax_insufficient = False
    w.is_novel_goal = False
    w.screen_content_class = "unknown"
    w.text_heavy_score = 0.0
    w.ax_text_ratio = 1.0
    return w


@pytest.fixture
def mock_ax_screen(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch accessibility.screen_context for world_model.verify tests."""
    data: dict[str, Any] = {
        "frontmost_app": "Safari",
        "bundle_id": "com.apple.Safari",
        "elements": [{"idx": 1, "role": "button", "title": "Go"}],
        "rendered": "Safari toolbar Go button",
        "element_count": 8,
    }

    def _screen_context(**_kwargs: Any) -> dict[str, Any]:
        return dict(data)

    monkeypatch.setattr(
        "aether.perception.accessibility.screen_context",
        _screen_context,
    )
    return data


@pytest.fixture
def sidecar_client(monkeypatch: pytest.MonkeyPatch) -> Generator[Any, None, None]:
    """FastAPI TestClient with optional auth token disabled."""
    monkeypatch.delenv("AETHER_SIDECAR_TOKEN", raising=False)
    from fastapi.testclient import TestClient
    from sidecar import server

    with TestClient(server.app) as client:
        yield client
