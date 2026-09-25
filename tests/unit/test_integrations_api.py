"""GET/POST /integrations."""
from __future__ import annotations

import pytest

from aether import integrations
from aether.ipc import native_effector


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("AETHER_DATA_DIR", str(tmp_path))


def test_list_all_off_by_default(sidecar_client) -> None:  # noqa: ANN001
    r = sidecar_client.get("/integrations").json()
    assert len(r["integrations"]) == 5
    assert all(not e["enabled"] for e in r["integrations"])
    # The app isn't reachable from this test process.
    assert r["os_status"] is None


def test_set_enabled_round_trips(sidecar_client) -> None:  # noqa: ANN001
    r = sidecar_client.post("/integrations/calendar", json={"enabled": True}).json()
    assert r["enabled"] is True and r["id"] == "calendar"
    assert integrations.enabled("calendar")
    r2 = sidecar_client.get("/integrations").json()
    cal = next(e for e in r2["integrations"] if e["id"] == "calendar")
    assert cal["enabled"] is True


def test_set_enabled_unknown_id_404(sidecar_client) -> None:  # noqa: ANN001
    r = sidecar_client.post("/integrations/bogus", json={"enabled": True})
    assert r.status_code == 404


def test_os_status_reflects_native_effector(sidecar_client, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(native_effector, "pim",
                        lambda action, args=None: {"calendar": "authorized"})
    r = sidecar_client.get("/integrations").json()
    assert r["os_status"] == {"calendar": "authorized"}
