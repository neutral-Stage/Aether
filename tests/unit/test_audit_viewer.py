"""Audit log: chain verification past 500 records, concurrent writers, the viewer's reader."""
from __future__ import annotations

import json
import threading

from aether.core.audit_log import AuditLog

KEY = b"test-key-32-bytes-long!!!!!!!!!!"


def test_a_long_log_verifies(tmp_path) -> None:  # noqa: ANN001
    log = AuditLog(path=tmp_path / "a.jsonl", enabled=True, hmac_key=KEY)
    for i in range(620):
        log.record("action", run_id="r", tool="click", summary=f"step {i}")
    assert log.verify_chain() == (True, "ok (620 records)")
    assert log.verify_chain(max_records=50) == (True, "ok (last 50 of 620 records)")


def test_concurrent_writers_keep_one_chain(tmp_path) -> None:  # noqa: ANN001
    log = AuditLog(path=tmp_path / "c.jsonl", enabled=True, hmac_key=KEY)

    def write(tag: str) -> None:
        for i in range(150):
            log.record("action", summary=f"{tag} {i}")

    threads = [threading.Thread(target=write, args=(t,)) for t in "abcd"]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ok, msg = log.verify_chain()
    assert ok, msg


def test_tampering_is_found(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "t.jsonl"
    log = AuditLog(path=path, enabled=True, hmac_key=KEY)
    for i in range(5):
        log.record("confirmation", tool="run_shell", confirmed=False, summary=f"ask {i}")
    lines = path.read_text().splitlines()
    rec = json.loads(lines[2])
    rec["confirmed"] = True
    lines[2] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")
    ok, msg = log.verify_chain()
    assert not ok and "record 3" in msg
    path.write_text("\n".join(lines[:2] + ["{not json"] + lines[3:]) + "\n")
    assert log.verify_chain() == (False, "record 3 is not readable")
    del lines[2]
    path.write_text("\n".join(lines) + "\n")
    ok, msg = log.verify_chain()
    assert not ok and "chain break at record 3" in msg


def test_recent_filters_newest_first(tmp_path) -> None:  # noqa: ANN001
    log = AuditLog(path=tmp_path / "r.jsonl", enabled=True, hmac_key=KEY)
    log.record("run_start", run_id="r1", summary="Open Downloads")
    log.record("action", run_id="r1", tool="open_path", summary="opened ~/Downloads")
    log.record("confirmation", run_id="r2", tool="run_shell", confirmed=True, summary="make")
    rows = log.recent()
    assert [r["event"] for r in rows] == ["confirmation", "action", "run_start"]
    assert "hmac" not in rows[0] and len(rows[0]["id"]) == 12
    assert [r["event"] for r in log.recent(query="downloads")] == ["action", "run_start"]
    assert [r["run_id"] for r in log.recent(event="confirmation")] == ["r2"]
    assert len(log.recent(run_id="r1", limit=1)) == 1


def test_api(sidecar_client, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from sidecar import server

    log = AuditLog(path=tmp_path / "api.jsonl", enabled=True, hmac_key=KEY)
    log.record("action", tool="click", summary="clicked Save")
    monkeypatch.setattr(server, "_audit", lambda: log)
    rows = sidecar_client.get("/audit", params={"q": "save"}).json()["entries"]
    assert rows[0]["summary"] == "clicked Save"
    assert sidecar_client.get("/audit/verify").json()["ok"] is True
