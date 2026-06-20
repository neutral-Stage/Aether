"""Sidecar HTTP integration tests (Phase 6)."""
from __future__ import annotations


import pytest


@pytest.mark.integration
class TestSidecarHealth:
    def test_health_ok(self, sidecar_client) -> None:
        resp = sidecar_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["service"] == "aether-sidecar"
        assert "audit" in data
        assert "key_source" in data["audit"]
        assert "beta_flags" in data

    def test_metrics_snapshot(self, sidecar_client) -> None:
        resp = sidecar_client.get("/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "counters" in data
        assert "histograms" in data

    def test_audit_verify(self, sidecar_client) -> None:
        resp = sidecar_client.get("/audit/verify")
        assert resp.status_code == 200
        data = resp.json()
        assert "ok" in data
        assert "path" in data

    def test_status_idle(self, sidecar_client) -> None:
        resp = sidecar_client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "idle"

    def test_voice_config(self, sidecar_client) -> None:
        resp = sidecar_client.get("/config/voice")
        assert resp.status_code == 200
        data = resp.json()
        assert "stt" in data
        assert "barge_in" in data


@pytest.mark.integration
class TestSidecarTools:
    def test_tool_schemas_manifest(self, sidecar_client) -> None:
        resp = sidecar_client.get("/tools/schemas")
        assert resp.status_code == 200
        manifest = resp.json()
        assert "tools" in manifest
        assert len(manifest["tools"]) >= 19

    def test_tool_schema_by_name(self, sidecar_client) -> None:
        resp = sidecar_client.get("/tools/schemas/finish")
        assert resp.status_code == 200
        schema = resp.json()
        assert schema["name"] == "finish"


@pytest.mark.integration
class TestSidecarMutations:
    def test_stop_without_run(self, sidecar_client) -> None:
        resp = sidecar_client.post("/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"

    def test_run_requires_goal(self, sidecar_client) -> None:
        resp = sidecar_client.post("/run", json={"goal": ""})
        assert resp.status_code == 400

    def test_run_rejects_without_llm_key(self, sidecar_client) -> None:
        resp = sidecar_client.post(
            "/run",
            json={"goal": "Open Finder", "stream": False},
        )
        # No API keys in test env → 400 unless local_only
        assert resp.status_code in (400, 409)


@pytest.mark.integration
class TestSidecarAuth:
    def test_mutating_endpoints_require_token_when_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AETHER_SIDECAR_TOKEN", "test-secret-token")
        from fastapi.testclient import TestClient
        from sidecar import server

        with TestClient(server.app) as client:
            resp = client.post("/stop")
            assert resp.status_code == 401

            resp = client.post(
                "/stop",
                headers={"Authorization": "Bearer test-secret-token"},
            )
            assert resp.status_code == 200

    def test_read_endpoints_skip_auth_when_token_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AETHER_SIDECAR_TOKEN", "test-secret-token")
        from fastapi.testclient import TestClient
        from sidecar import server

        with TestClient(server.app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200

    def test_metrics_dashboard_require_auth_when_token_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AETHER_SIDECAR_TOKEN", "test-secret-token")
        from fastapi.testclient import TestClient
        from sidecar import server

        with TestClient(server.app) as client:
            assert client.get("/metrics").status_code == 401
            assert client.get("/dashboard").status_code == 401
            headers = {"Authorization": "Bearer test-secret-token"}
            assert client.get("/metrics", headers=headers).status_code == 200
            assert client.get("/dashboard", headers=headers).status_code == 200
