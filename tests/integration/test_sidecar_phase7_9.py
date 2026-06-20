"""Tests for sidecar Phase 7–9 endpoints."""
from __future__ import annotations

import pytest


@pytest.mark.integration
class TestSidecarPhase7Endpoints:
    def test_beta_config(self, sidecar_client) -> None:
        resp = sidecar_client.get("/config/beta")
        assert resp.status_code == 200
        data = resp.json()
        assert "ambient_listening" in data
        assert "wake_word" in data
        assert "screen_stream_fps" in data

    def test_voice_metrics(self, sidecar_client) -> None:
        resp = sidecar_client.post(
            "/metrics/voice",
            json={"stt_ms": 120.0, "tts_ms": 340.0, "voice_rtt_ms": 900.0},
        )
        assert resp.status_code == 200
        metrics = sidecar_client.get("/metrics").json()
        hist = metrics.get("histograms", {})
        assert "voice_rtt_ms" in hist

    def test_confirm_unknown_id(self, sidecar_client) -> None:
        resp = sidecar_client.post(
            "/confirm",
            json={"request_id": "nope", "approved": True},
        )
        assert resp.status_code == 404


@pytest.mark.integration
class TestSidecarPhase8Endpoints:
    def test_percept_screen_post(self, sidecar_client) -> None:
        resp = sidecar_client.post(
            "/percept/screen",
            json={"width": 1920, "height": 1080, "fps": 0.5, "note": "test"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        latest = sidecar_client.get("/percept/screen").json()
        assert latest.get("width") == 1920


@pytest.mark.integration
class TestSidecarPhase9Endpoints:
    def test_feedback(self, sidecar_client, tmp_path, monkeypatch) -> None:
        store = tmp_path / "feedback.jsonl"
        monkeypatch.setenv("AETHER_CONFIG_PATH", str(tmp_path / "missing.yaml"))

        from aether.core.config import Config

        cfg = Config(
            raw={"feedback": {"enabled": True, "store_path": str(store)}},
            anthropic_api_key=None,
            openai_api_key=None,
            api_keys={},
        )

        def _load():
            return cfg

        monkeypatch.setattr("sidecar.server.load_config", _load)
        resp = sidecar_client.post(
            "/feedback",
            json={"message": "Great beta!", "category": "praise"},
        )
        assert resp.status_code == 200
        assert store.exists()
