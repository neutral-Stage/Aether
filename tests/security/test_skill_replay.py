"""Skill replay policy gate and sidecar defaults (P1 hardening)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aether.core.policy import Policy, PolicyConfig
from aether.memory.skills import SkillStore
from aether.tools.registry import AgentContext, Registry, ToolSpec


@pytest.mark.security
class TestSkillReplayPolicy:
    def test_replay_blocks_without_confirmation(self, tmp_path) -> None:
        store = SkillStore(tmp_path / "skills.db")
        reg = Registry()
        reg.register(ToolSpec(
            name="run_shell",
            json_schema={"type": "object", "properties": {"command": {"type": "string"}}},
            permission="shell",
            impact="destructive",
            handler=lambda _a, _c: "ok",
        ))
        trace = [
            {"tool": "run_shell", "args": {"command": "echo hi"}},
            {"tool": "finish", "args": {}},
        ]
        skill_id = store.distill_from_trace("echo test", trace)
        policy = Policy(PolicyConfig(careful=True))
        result = store.replay(
            skill_id,
            {},
            dispatch=reg.dispatch,
            ctx=AgentContext(),
            policy=policy,
            registry=reg,
        )
        assert not result.success
        assert "confirmation required" in (result.error or "")
        store.close()

    def test_replay_async_honors_declined_confirmation(self, tmp_path) -> None:
        import asyncio

        store = SkillStore(tmp_path / "skills.db")
        reg = Registry()
        reg.register(ToolSpec(
            name="open_app",
            json_schema={"type": "object", "properties": {"name": {"type": "string"}}},
            permission="accessibility",
            impact="reversible",
            handler=lambda _a, _c: "opened",
        ))
        trace = [
            {"tool": "open_app", "args": {"name": "Finder"}},
            {"tool": "finish", "args": {}},
        ]
        skill_id = store.distill_from_trace("open Finder", trace)
        policy = Policy(PolicyConfig(careful=True))
        confirm = AsyncMock(return_value=False)
        result = asyncio.run(store.replay_async(
            skill_id,
            {},
            dispatch=reg.dispatch,
            ctx=AgentContext(),
            policy=policy,
            registry=reg,
            confirm=confirm,
        ))
        assert not result.success
        assert "declined" in (result.error or "")
        store.close()


@pytest.mark.security
class TestSkillReplaySidecar:
    def test_replay_request_defaults_to_orchestrator(self) -> None:
        from sidecar.server import SkillReplayRequest

        body = SkillReplayRequest()
        assert body.via_orchestrator is True

    def test_feedback_requires_auth_when_token_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AETHER_SIDECAR_TOKEN", "secret")
        from fastapi.testclient import TestClient
        from sidecar import server

        with TestClient(server.app) as client:
            resp = client.post("/feedback", json={"message": "hello"})
            assert resp.status_code == 401
            resp = client.post(
                "/feedback",
                json={"message": "hello"},
                headers={"Authorization": "Bearer secret"},
            )
            assert resp.status_code in (200, 503)

    def test_feedback_rejects_oversized_message(
        self,
        sidecar_client,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from aether.core.config import Config

        cfg = Config(
            raw={"feedback": {"enabled": True, "store_path": "/tmp/feedback.jsonl"}},
            anthropic_api_key=None,
            openai_api_key=None,
            api_keys={},
        )
        monkeypatch.setattr("sidecar.server.load_config", lambda *a, **k: cfg)
        resp = sidecar_client.post(
            "/feedback",
            json={"message": "x" * 2500},
        )
        assert resp.status_code == 400
