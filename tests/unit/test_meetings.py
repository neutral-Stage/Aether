"""Meeting notes: store, notes parsing, the API (consent-gated start, audio, stop), tools."""
from __future__ import annotations

import io
import json
import stat
import wave
from types import SimpleNamespace

import pytest

from aether import meetings
from aether.meetings import MeetingStore, parse_notes, render_notes, transcript_for_model
from aether.tools.registry import DEFAULT_REGISTRY as R
from aether.tools.registry import AgentContext


def _wav(seconds: float = 0.5) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buf.getvalue()


def test_store_segments_search_and_privacy(tmp_path) -> None:  # noqa: ANN001
    s = MeetingStore(tmp_path / "m.db")
    assert stat.S_IMODE(s.path.stat().st_mode) == 0o600
    mid = s.create("Budget sync", "zoom.us", started=1000.0)
    s.add_segment(mid, "them", "We agreed to move the launch to March.", 1030.0)
    s.add_segment(mid, "me", "  I'll update   the roadmap. ", 1065.0)
    assert s.add_segment(mid, "everyone", "x", 1.0) == 0 and s.add_segment(mid, "me", " ", 1.0) == 0
    assert transcript_for_model(s.segments(mid), 1000.0) == (
        "[00:30] Them: We agreed to move the launch to March.\n[01:05] Me: I'll update the roadmap.")
    hits = s.search("launch march")
    assert hits[0]["id"] == mid and "[launch]" in hits[0]["snippet"].lower()
    s.end(mid, 2000.0)
    assert s.meeting(mid)["ended"] == 2000.0
    assert s.list()[0]["segments"] == 2
    assert s.delete(mid) and s.meeting(mid) is None and s.search("launch") == []


def test_parse_and_render_notes() -> None:
    reply = json.dumps({"summary": "We moved the launch.", "decisions": ["Launch in March", 3],
                        "action_items": [{"task": "Update the roadmap", "owner": "Me",
                                          "due": "Friday"}, {"owner": "x"}, "bad"]})
    notes = parse_notes("Here you go: " + reply)
    assert notes == {"summary": "We moved the launch.", "decisions": ["Launch in March"],
                     "action_items": [{"task": "Update the roadmap", "owner": "Me",
                                       "due": "Friday"}]}
    text = render_notes("Budget sync", 0.0, notes)
    assert "Decisions:\n- Launch in March" in text and "- Update the roadmap (Me, Friday)" in text
    assert parse_notes('{"summary": ""}') is None and parse_notes("nope") is None


@pytest.fixture
def api(monkeypatch, tmp_path):  # noqa: ANN001, ANN201
    from sidecar import meetings_api

    meetings.reset()
    raw = {"meetings": {"transcription": "local", "db_path": str(tmp_path / "meet.db")}}
    cfg = SimpleNamespace(raw=raw, get=lambda k, default=None: raw.get(k, default), stt="groq",
                          stt_model="whisper", groq_api_key="g", openai_api_key=None,
                          has_cloud_llm=lambda: True)
    monkeypatch.setattr(meetings_api, "load_config", lambda validate=True: cfg)
    monkeypatch.setattr(meetings, "get_store",
                        lambda raw_config=None, _s=MeetingStore(tmp_path / "meet.db"): _s)
    ready = {"local": True}
    monkeypatch.setattr("aether.voice.stt_local.LocalSTT.available",
                        lambda self: ready["local"])
    said = ["We agreed to move the launch to March.", "I'll update the roadmap."]
    kept: list[str] = []

    def fake_transcribe(cfg, engine, wav):  # noqa: ANN001, ANN202
        kept.append(engine)
        return said.pop(0)

    monkeypatch.setattr(meetings_api, "_transcribe", fake_transcribe)
    monkeypatch.setattr(meetings_api, "_summarize", lambda cfg, transcript: parse_notes(json.dumps(
        {"summary": "Launch moves to March.", "decisions": ["March launch"],
         "action_items": [{"task": "Update the roadmap", "owner": "Me", "due": ""}]})))
    yield SimpleNamespace(cfg=cfg, ready=ready, engines=kept)
    meetings.reset()


def test_meeting_flow(sidecar_client, api) -> None:  # noqa: ANN001
    status = sidecar_client.get("/meetings/transcription").json()
    assert status == {"engine": "local", "ready": True, "message": "", "summarize": True}
    started = sidecar_client.post("/meetings", json={"app": "zoom.us"}).json()
    mid = started["id"]
    assert started["title"] == "zoom.us meeting"
    r = sidecar_client.post(f"/meetings/{mid}/audio", params={"channel": "them", "offset_s": 30},
                            content=_wav())
    assert r.json()["text"].startswith("We agreed")
    sidecar_client.post(f"/meetings/{mid}/audio", params={"channel": "me", "offset_s": 60},
                        content=_wav())
    assert api.engines == ["local", "local"]
    assert sidecar_client.post(f"/meetings/{mid}/audio", params={"channel": "all"},
                               content=_wav()).status_code == 400
    assert sidecar_client.post(f"/meetings/{mid}/audio", params={"channel": "me"},
                               content=b"not audio").status_code == 400
    done = sidecar_client.post(f"/meetings/{mid}/stop").json()
    assert done["notes"]["summary"] == "Launch moves to March."
    assert "Action items:\n- Update the roadmap (Me)" in done["text"]
    assert sidecar_client.post(f"/meetings/{mid}/audio", params={"channel": "me"},
                               content=_wav()).status_code == 409
    full = sidecar_client.get(f"/meetings/{mid}").json()
    assert full["transcript"].startswith("[00:30] Them: We agreed")
    assert sidecar_client.get("/meetings").json()["meetings"][0]["id"] == mid
    assert sidecar_client.delete(f"/meetings/{mid}").json() == {"deleted": True}


def test_no_start_without_a_transcriber(sidecar_client, api) -> None:  # noqa: ANN001
    api.ready["local"] = False
    status = sidecar_client.get("/meetings/transcription").json()
    assert not status["ready"] and "mlx-whisper" in status["message"]
    assert sidecar_client.post("/meetings", json={"app": "zoom.us"}).status_code == 501
    # cloud mode uses the voice.stt provider
    api.cfg.raw["meetings"]["transcription"] = "cloud"
    assert sidecar_client.get("/meetings/transcription").json()["engine"] == "groq"


def test_tools(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    store = MeetingStore(tmp_path / "t.db")
    monkeypatch.setattr(meetings, "get_store", lambda raw_config=None: store)
    mid = store.create("Design review", "Microsoft Teams", started=1000.0)
    store.add_segment(mid, "them", "The new onboarding flow ships next sprint.", 1010.0)
    store.set_notes(mid, "Onboarding ships next sprint.",
                    {"summary": "Onboarding ships next sprint.", "decisions": [],
                     "action_items": []})
    out = R.dispatch("search_meetings", {"query": "onboarding"}, AgentContext())
    assert mid in out and "<meeting_notes>" in out
    assert "Design review" in R.dispatch("search_meetings", {}, AgentContext())
    notes = R.dispatch("meeting_notes", {"id": mid}, AgentContext())
    assert "Onboarding ships next sprint." in notes and "[00:10] Them:" in notes
    assert R.dispatch("meeting_notes", {"id": "nope"}, AgentContext()).startswith("ERROR")
