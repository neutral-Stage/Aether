"""Talk cancellation: abort_event plumbing in aether/talk.py, the /talk/{id}/cancel
endpoint, STOP's cancel_all(), and the speculative-talk metrics counters."""
from __future__ import annotations

import threading

import pytest

from aether import talk
from aether.core.config import Config
from aether.core.llm import LLMResponse
from aether.core.metrics import MetricsCollector
from aether.core.stop import StopRequested
from aether.perception import accessibility as ax
from aether.perception import screen


def el(idx: int, title: str, x: float, y: float, w: float = 80, h: float = 24,
       role: str = "AXButton") -> ax.Element:
    return ax.Element(idx, role, title, "", True, x, y, w, h)


def cap_for(path: str, w: int = 1440, h: int = 900, x0: float = 0.0) -> screen.Capture:
    disp = screen.DisplayInfo(1, 1, x0, 0.0, 1440.0, 900.0, 2.0, True, True)
    return screen._register(screen.Capture(path, disp, w, h, 1, True, "native"))  # noqa: SLF001


class FakeClient:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list = []

    def step(self, system, messages, tools, *, abort_event=None):  # noqa: ANN001
        self.calls.append((system, messages))
        return LLMResponse(text=self.replies.pop(0), tool_calls=[], raw_content=[],
                           stop_reason="end_turn", backend="fake")


@pytest.fixture
def scene(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    from PIL import Image

    path = str(tmp_path / "full.png")
    Image.new("RGB", (1440, 900), "white").save(path)
    cap = cap_for(path)
    monkeypatch.setattr(screen, "grounding_settings", lambda: {"coord_space": "pixels"})
    monkeypatch.setattr(ax, "resolve_app", lambda name: {"pid": 7, "name": name})
    tree = [el(1, "Bluetooth", 100, 100), el(2, "Wi-Fi", 100, 140)]
    return talk.build_scene(
        (110, 110), capture=lambda d: cap,
        element_at=lambda x, y: {"element": {"role": "AXButton", "title": "Bluetooth"},
                                 "ancestry": [], "window": "Settings",
                                 "app": "System Settings", "url": ""},
        read_tree=lambda **k: tree, selected_text=lambda: "")


# ---- aether.talk.ask ----------------------------------------------------------------------------

def test_ask_raises_and_leaves_history_untouched_when_preset(scene) -> None:  # noqa: ANN001
    client = FakeClient(["Click this switch. [POINT:e1:Bluetooth]"])
    event = threading.Event()
    event.set()
    sess = talk.get_session("cancel-preset")
    assert len(sess.history) == 0
    with pytest.raises(StopRequested):
        talk.ask("where is bluetooth?", client, scene=scene, session_id="cancel-preset",
                 abort_event=event)
    assert len(sess.history) == 0
    # not even the (wasted) model call is skipped — the check comes after it,
    # so the caller still doesn't pay for a refine pass or a committed turn.
    assert len(client.calls) == 1


def test_ask_unaffected_by_a_clear_event(scene) -> None:  # noqa: ANN001
    client = FakeClient(["Click this switch. [POINT:e1:Bluetooth]"])
    reply = talk.ask("where is bluetooth?", client, scene=scene, session_id="cancel-clear",
                     abort_event=threading.Event())
    assert reply.answer == "Click this switch."
    assert len(talk.get_session("cancel-clear").history) == 1



class StreamingClient:
    """Streams its reply one chunk at a time through on_token."""

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.sent = 0

    def step(self, system, messages, tools, *, abort_event=None, on_token=None):  # noqa: ANN001
        for chunk in self.chunks:
            self.sent += 1
            if on_token is not None:
                on_token(chunk)
        return LLMResponse(text="".join(self.chunks), tool_calls=[], raw_content=[],
                           stop_reason="end_turn", backend="fake")


def test_cancel_stops_the_stream_at_the_next_chunk(scene) -> None:  # noqa: ANN001
    client = StreamingClient(["Open ", "Settings. ", "Then ", "click ", "Bluetooth. ", "Done."])
    event = threading.Event()
    heard: list[str] = []

    def on_text(text: str) -> None:
        heard.append(text)
        event.set()                      # cancelled right after the first spoken text

    with pytest.raises(StopRequested):
        talk.ask("how do I turn on bluetooth?", client, scene=scene, session_id="cancel-stream",
                 on_text=on_text, abort_event=event)
    assert client.sent < len(client.chunks)
    assert len(talk.get_session("cancel-stream").history) == 0

# ---- the sidecar endpoint, the registry, and STOP integration -----------------------------------

def test_talk_endpoint_cancel_midflight(sidecar_client, monkeypatch, scene) -> None:  # noqa: ANN001
    from sidecar import talk_api

    monkeypatch.setattr(Config, "has_cloud_llm", lambda self: True)
    monkeypatch.setattr(talk_api, "_client", lambda cfg: FakeClient([]))

    started = threading.Event()

    def fake_ask(question, client, *, cursor=None, session_id=None, refine=True,
                scene=None, redact=None, on_text=None, abort_event=None):  # noqa: ANN001
        started.set()
        got = abort_event.wait(timeout=5.0)
        assert got and abort_event.is_set(), "cancel was never observed"
        raise StopRequested("cancelled for test")

    monkeypatch.setattr(talk_api.talk, "ask", fake_ask)
    sent: list = []

    async def capture(ev):  # noqa: ANN001, ANN202
        sent.append(ev)

    monkeypatch.setattr(talk_api, "_broadcast", capture)

    result: dict = {}

    def run_post() -> None:
        result["resp"] = sidecar_client.post(
            "/talk", json={"question": "where is bluetooth", "talk_id": "cancel-mid"})

    thread = threading.Thread(target=run_post)
    thread.start()
    try:
        assert started.wait(timeout=2.0), "the endpoint never called talk.ask"
        cancel_resp = sidecar_client.post("/talk/cancel-mid/cancel")
    finally:
        thread.join(timeout=5.0)
    assert not thread.is_alive()

    assert cancel_resp.status_code == 200
    assert cancel_resp.json() == {"cancelled": True}
    assert result["resp"].status_code == 200
    assert result["resp"].json() == {"cancelled": True, "talk_id": "cancel-mid"}
    assert sent == []   # neither talk_done nor pointer, and no talk_token after cancel


def test_cancel_endpoint_unknown_talk_id_is_a_noop(sidecar_client) -> None:  # noqa: ANN001
    resp = sidecar_client.post("/talk/does-not-exist/cancel")
    assert resp.status_code == 200
    assert resp.json() == {"cancelled": False}


def test_cancel_all_sets_every_registered_event() -> None:
    from sidecar import talk_api

    e1, e2 = threading.Event(), threading.Event()
    with talk_api._active_lock:  # noqa: SLF001
        talk_api._active["a1"] = e1  # noqa: SLF001
        talk_api._active["a2"] = e2  # noqa: SLF001
    try:
        assert talk_api.cancel_all() >= 2
        assert e1.is_set() and e2.is_set()
    finally:
        with talk_api._active_lock:  # noqa: SLF001
            talk_api._active.pop("a1", None)  # noqa: SLF001
            talk_api._active.pop("a2", None)  # noqa: SLF001


def test_stop_endpoint_cancels_active_talks(sidecar_client) -> None:  # noqa: ANN001
    from sidecar import talk_api

    event = threading.Event()
    with talk_api._active_lock:  # noqa: SLF001
        talk_api._active["stop-cancel"] = event  # noqa: SLF001
    try:
        assert sidecar_client.post("/stop").status_code == 200
        assert event.is_set()
    finally:
        with talk_api._active_lock:  # noqa: SLF001
            talk_api._active.pop("stop-cancel", None)  # noqa: SLF001


def test_speculative_metrics_counters(sidecar_client, monkeypatch, scene) -> None:  # noqa: ANN001
    from sidecar import talk_api

    monkeypatch.setattr(Config, "has_cloud_llm", lambda self: True)
    monkeypatch.setattr(talk_api, "_client",
                        lambda cfg: FakeClient(["Here. [POINT:e1:Bluetooth]"]))
    monkeypatch.setattr(talk, "build_scene", lambda cursor, redact=None: scene)
    monkeypatch.setattr(talk_api, "_broadcast", None)

    resp = sidecar_client.post("/talk", json={"question": "where is bluetooth", "x": 110,
                                              "y": 110, "speculative": True})
    assert resp.status_code == 200
    counters = MetricsCollector.get().snapshot()["counters"]
    assert counters.get("talk_speculative_used") == 1
    assert "talk_speculative_wasted" not in counters


def test_speculative_wasted_metric_on_cancel(sidecar_client, monkeypatch, scene) -> None:  # noqa: ANN001
    from sidecar import talk_api

    monkeypatch.setattr(Config, "has_cloud_llm", lambda self: True)
    monkeypatch.setattr(talk_api, "_client", lambda cfg: FakeClient([]))

    def fake_ask(question, client, *, cursor=None, session_id=None, refine=True,
                scene=None, redact=None, on_text=None, abort_event=None):  # noqa: ANN001
        raise StopRequested("cancelled before first token")

    monkeypatch.setattr(talk_api.talk, "ask", fake_ask)
    monkeypatch.setattr(talk_api, "_broadcast", None)

    resp = sidecar_client.post("/talk", json={"question": "where is bluetooth",
                                              "talk_id": "wasted", "speculative": True})
    assert resp.json() == {"cancelled": True, "talk_id": "wasted"}
    counters = MetricsCollector.get().snapshot()["counters"]
    assert counters.get("talk_cancelled") == 1
    assert counters.get("talk_speculative_wasted") == 1



def test_cancelling_a_finished_speculation_forgets_its_turn(sidecar_client, monkeypatch,  # noqa: ANN001
                                                          scene) -> None:
    from sidecar import talk_api

    monkeypatch.setattr(Config, "has_cloud_llm", lambda self: True)
    monkeypatch.setattr(talk_api, "_client",
                        lambda cfg: FakeClient(["Here. [POINT:e1:Bluetooth]"]))
    monkeypatch.setattr(talk, "build_scene", lambda cursor, redact=None: scene)
    monkeypatch.setattr(talk_api, "_broadcast", None)
    r = sidecar_client.post("/talk", json={"question": "where is bluetooth", "x": 110, "y": 110,
                                           "talk_id": "spec1", "session_id": "forget-me",
                                           "speculative": True, "stream": False}).json()
    assert r["answer"] == "Here."
    assert len(talk.get_session("forget-me").history) == 1
    out = sidecar_client.post("/talk/spec1/cancel").json()
    assert out == {"cancelled": True, "forgotten": True}
    assert len(talk.get_session("forget-me").history) == 0
    assert sidecar_client.post("/talk/spec1/cancel").json() == {"cancelled": False}
