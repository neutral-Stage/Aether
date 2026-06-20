"""Unit tests for model router heuristics (Phase 6)."""
from __future__ import annotations

import pytest

from aether.core.router import RouteTier, Router, RouterConfig
from aether.core.world_model import WorldModel


@pytest.fixture
def router() -> Router:
    cfg = RouterConfig.load()
    r = Router(router_cfg=cfg, anthropic_api_key=None, api_keys={})
    r._local_available = True  # noqa: SLF001 — test routing without Ollama
    return r


@pytest.mark.unit
class TestRouter:
    def test_failure_threshold_routes_to_cloud(self, router: Router, world: WorldModel) -> None:
        world.step_failure_count = 3
        decision = router.route(world)
        assert decision.tier == RouteTier.CLOUD_FRONTIER
        assert "failures" in decision.reason

    def test_ax_insufficient_routes_to_vision(self, router: Router, world: WorldModel) -> None:
        world.element_count = 0
        world.ax_insufficient = True
        decision = router.route(world)
        assert decision.tier == RouteTier.VISION
        assert decision.use_vision_context

    def test_careful_mode_routes_to_cloud(self, router: Router, world: WorldModel) -> None:
        world.element_count = 10
        decision = router.route(world, careful=True)
        assert decision.tier == RouteTier.CLOUD_FRONTIER
        assert "careful" in decision.reason

    def test_reflexive_path_when_ax_ok(self, router: Router, world: WorldModel) -> None:
        world.element_count = 10
        world.is_novel_goal = False
        world.needs_replan = False
        decision = router.route(world)
        assert decision.tier == RouteTier.LOCAL_FAST

    def test_force_local_only(self, router: Router, world: WorldModel) -> None:
        decision = router.route(world, force_local=True)
        assert decision.tier == RouteTier.LOCAL_FAST
        assert "local_only" in decision.reason

    def test_needs_replan_routes_to_cloud(self, router: Router, world: WorldModel) -> None:
        world.element_count = 10
        world.needs_replan = True
        decision = router.route(world)
        assert decision.tier == RouteTier.CLOUD_FRONTIER

    def test_is_reflexive_tool(self, router: Router) -> None:
        reflexive = router.cfg.routing.get("reflexive_tools") or []
        if reflexive:
            assert router.is_reflexive_tool(reflexive[0])
        assert not router.is_reflexive_tool("finish")
