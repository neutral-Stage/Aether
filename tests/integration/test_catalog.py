"""Discoverability: GET /catalog lists supported apps + tools (Phase 7)."""
from __future__ import annotations

import pytest


@pytest.mark.integration
class TestCatalog:
    def test_catalog_lists_tools(self, sidecar_client) -> None:
        data = sidecar_client.get("/catalog").json()
        assert data["tool_count"] > 0
        names = {t["name"] for t in data["tools"]}
        assert "get_screen_context" in names
        assert "open_app" in names
        # tool entries carry impact/permission for the UI
        sample = data["tools"][0]
        assert {"name", "description", "impact", "permission"} <= set(sample)

    def test_catalog_lists_apps(self, sidecar_client) -> None:
        data = sidecar_client.get("/catalog").json()
        assert isinstance(data["apps"], list)
        assert len(data["apps"]) >= 20  # 33 packs ship bundled
        entry = data["apps"][0]
        assert "key" in entry and "app" in entry
