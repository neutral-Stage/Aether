"""Unit tests for skill store replay (Phase 11)."""
from __future__ import annotations

import pytest

from aether.memory.skills import SkillStore


@pytest.mark.unit
class TestSkillStore:
    def test_distill_and_list(self, tmp_path) -> None:
        db = tmp_path / "skills.db"
        store = SkillStore(db)
        trace = [
            {"tool": "open_app", "args": {"name": "Finder"}},
            {"tool": "finder_go_to", "args": {"path": "/Users/test/Downloads"}},
            {"tool": "finish", "args": {"message": "done"}},
        ]
        skill_id = store.distill_from_trace("Open Finder and go to Downloads", trace)
        assert skill_id is not None
        skills = store.list_skills()
        assert len(skills) == 1
        assert skills[0].id == skill_id
        store.close()

    def test_substitute_parameters(self, tmp_path) -> None:
        store = SkillStore(tmp_path / "skills.db")
        steps = [{"tool": "safari_open_url", "args": {"url": "{{param1}}"}}]
        resolved = store.substitute_parameters(steps, {"param1": "https://apple.com"})
        assert resolved[0]["args"]["url"] == "https://apple.com"
        store.close()

    def test_replay_dispatches_tools(self, tmp_path) -> None:
        store = SkillStore(tmp_path / "skills.db")
        trace = [
            {"tool": "mock_tool", "args": {"x": 1}},
            {"tool": "finish", "args": {}},
        ]
        skill_id = store.distill_from_trace("run mock", trace)
        calls: list[str] = []

        def dispatch(name, args, ctx):
            calls.append(name)
            return f"ok:{name}"

        result = store.replay(skill_id, {}, dispatch=dispatch, ctx=None)
        assert result.success
        assert "mock_tool" in calls
        store.close()

    def test_build_replay_goal(self, tmp_path) -> None:
        store = SkillStore(tmp_path / "skills.db")
        trace = [
            {"tool": "safari_open_url", "args": {"url": "https://example.com"}},
            {"tool": "finish", "args": {}},
        ]
        skill_id = store.distill_from_trace('Open "https://example.com"', trace)
        skill = store.get_skill(skill_id)
        assert skill is not None
        goal = store.build_replay_goal(skill, {"param1": "https://example.com"})
        assert "Replay learned skill" in goal
        store.close()
