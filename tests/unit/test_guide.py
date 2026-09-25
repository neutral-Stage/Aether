"""Guide mode: request detection, planning, step completion, the session loop."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from aether.core import stop as stop_ctl
from aether.core.llm import LLMResponse
from aether.guide import plan
from aether.guide.detect import Detector, Snapshot
from aether.guide.plan import GuideStep
from aether.guide.session import GuideSession
from aether.perception import accessibility as ax
from aether.perception.pointing import Target

# ---- requests and plans ---------------------------------------------------------------------


@pytest.mark.parametrize(("text", "goal"), [
    ("Show me how to add a printer", "add a printer"),
    ("hey aether, teach me how to split the screen?", "split the screen"),
    ("walk me through exporting a PDF", "exporting a PDF"),
    ("How do I turn on dark mode", "turn on dark mode"),
])
def test_guide_requests(text: str, goal: str) -> None:
    assert plan.is_guide_request(text)
    assert plan.guide_goal(text) == goal


@pytest.mark.parametrize("text", ["add a printer", "show me my downloads", "open Safari"])
def test_not_guide_requests(text: str) -> None:
    assert not plan.is_guide_request(text)


def test_parse_steps_validates_and_caps() -> None:
    raw = {"steps": [
        {"say": "Open System Settings", "target": "", "done_when": "app",
         "expect": "System Settings"},
        {"say": "Click   Printers & Scanners", "target": "Printers & Scanners",
         "role": "row", "app": "System Settings", "done_when": "click"},
        {"say": "", "target": "x"},                              # dropped: nothing to say
        {"say": "Press the plus", "done_when": "teleport"},      # unknown → any
    ] + [{"say": f"step {i}"} for i in range(20)]}
    steps = plan.parse_steps("```json\n" + json.dumps(raw) + "\n```")
    assert len(steps) == plan.MAX_STEPS
    assert steps[1].say == "Click Printers & Scanners" and steps[1].role == "row"
    assert steps[2].done_when == "any"
    assert plan.parse_steps("I can't help with that.") == []


def test_plan_steps_sends_screen_and_notes() -> None:
    seen = {}

    class Client:
        def step(self, system, messages, tools, *, abort_event=None):  # noqa: ANN001
            seen["system"], seen["user"] = system, messages[0]["content"]
            return LLMResponse(text='{"steps": [{"say": "Click Wi-Fi", "target": "Wi-Fi"}]}',
                               tool_calls=[], raw_content=[], stop_reason="end_turn",
                               backend="fake")

    steps = plan.plan_steps("join a network", Client(), screen_summary="[1] AXRow 'Wi-Fi'",
                            pack_hint="use the sidebar")
    assert steps[0].target == "Wi-Fi"
    assert "Task: join a network" in seen["user"] and "use the sidebar" in seen["user"]
    assert "done_when" in seen["system"]


# ---- step completion ------------------------------------------------------------------------------

def _el(title: str, x=100, y=100, w=80, h=20, value="") -> ax.Element:  # noqa: ANN001
    return ax.Element(1, "AXButton", title, value, True, x, y, w, h)


def _detector(step: GuideStep, state: dict) -> Detector:
    def find(name, role=None, app=None):  # noqa: ANN001, ANN202
        el = state.get("target")
        return (NS(element=el) if el is not None else None), [], ""

    return Detector(step, frontmost=lambda: state.get("front", "Finder"),
                    focused=lambda: state.get("focused", {}),
                    windows=lambda: tuple(state.get("windows", ())), find=find)


def test_click_step_with_click_tap() -> None:
    state = {"target": _el("Add")}
    det = _detector(GuideStep("Click Add", target="Add", done_when="click"), state)
    before = det.snapshot()
    now = det.snapshot()
    assert not det.done(before, now, [(500, 500)], clicks_seen=True)
    assert det.done(before, now, [(140, 110)], clicks_seen=True)
    assert det.done(before, now, [(98, 96)], clicks_seen=True)      # within the 6 pt pad


def test_click_step_without_tap_uses_state_changes() -> None:
    state = {"target": _el("Add"), "windows": ("Printers",)}
    det = _detector(GuideStep("Click Add", target="Add", done_when="click"), state)
    before = det.snapshot()
    assert not det.done(before, det.snapshot(), [])
    state["windows"] = ("Printers", "Add Printer")
    assert det.done(before, det.snapshot(), [])
    state2 = {"target": _el("Save as PDF")}
    det2 = _detector(GuideStep("Choose it", target="Save as PDF", done_when="menu"), state2)
    before2 = det2.snapshot()
    state2["target"] = None                      # the menu closed after the choice
    assert det2.done(before2, det2.snapshot(), [])


def test_app_window_focus_type_and_any() -> None:
    state: dict = {"front": "Finder", "target": _el("Name", value="")}
    app = _detector(GuideStep("Open Settings", done_when="app", expect="System Settings"), state)
    b = app.snapshot()
    state["front"] = "System Settings"
    assert app.done(b, app.snapshot(), [])
    win = _detector(GuideStep("Open it", done_when="window", expect="add printer"), state)
    state["windows"] = ("Add Printer",)
    assert win.done(win.snapshot(), win.snapshot(), [])
    foc = _detector(GuideStep("Click in Name", target="Name", done_when="focus"), state)
    b = foc.snapshot()
    assert not foc.done(b, foc.snapshot(), [])
    state["focused"] = {"role": "AXTextField", "title": "Name"}
    assert foc.done(b, foc.snapshot(), [])
    typ = _detector(GuideStep("Type HP", target="Name", done_when="type", expect="hp"), state)
    b = typ.snapshot()
    state["target"] = _el("Name", value="HP LaserJet")
    assert typ.done(b, typ.snapshot(), [])
    anyc = _detector(GuideStep("Press ⌘N", done_when="any"), state)
    b = anyc.snapshot()
    assert not anyc.done(b, anyc.snapshot(), [])
    state["windows"] = ("Add Printer", "New")
    assert anyc.done(b, anyc.snapshot(), [])


# ---- the session ------------------------------------------------------------------------------

class FakeDetector:
    """Completes each step after `after` polls unless told otherwise."""

    plan: list[int] = []

    def __init__(self, step: GuideStep) -> None:
        self.step = step
        self.polls = 0
        self.after = FakeDetector.plan.pop(0) if FakeDetector.plan else 1

    def snapshot(self) -> Snapshot:
        self.polls += 1
        return Snapshot(frontmost=str(self.polls))

    def done(self, before, now, clicks, clicks_seen=False) -> bool:  # noqa: ANN001
        return self.polls > self.after


STEPS = [GuideStep("Open Settings", done_when="app", expect="System Settings"),
         GuideStep("Click Printers", target="Printers"),
         GuideStep("Click Add", target="Add")]


def _session(events: list, steps=STEPS, **kw) -> GuideSession:  # noqa: ANN001, ANN003
    return GuideSession("add a printer", list(steps), emit=events.append,
                        detector_factory=FakeDetector,
                        resolve=lambda s: Target("point", 10, 20, s.target, source="ax")
                        if s.target else None, poll=0.01, **kw)


def test_session_walks_every_step() -> None:
    FakeDetector.plan = [1, 2, 1]
    events: list = []
    status = asyncio.run(_session(events).run())
    assert status == "done"
    steps = [e for e in events if e["type"] == "guide_step"]
    assert [e["index"] for e in steps] == [0, 1, 2]
    assert steps[0]["target"] is None and steps[1]["target"]["label"] == "Printers"
    assert events[-1] == {"type": "guide_done", "status": "done", "total": 3,
                          "guide_id": events[-1]["guide_id"]}


def test_controls_back_repeat_and_stop() -> None:
    FakeDetector.plan = [10_000] * 10
    events: list = []

    async def scenario() -> str:
        s = _session(events)
        task = asyncio.ensure_future(s.run())
        await asyncio.sleep(0.05)
        assert s.control("next")
        await asyncio.sleep(0.05)
        assert s.control("back")
        await asyncio.sleep(0.05)
        assert s.control("repeat")
        await asyncio.sleep(0.05)
        assert not s.control("teleport")
        assert s.control("stop")
        return await task

    assert asyncio.run(scenario()) == "stopped"
    indexes = [e["index"] for e in events if e["type"] == "guide_step"]
    assert indexes == [0, 1, 0, 0]
    assert events[-1]["status"] == "stopped"


def test_do_it_for_me_hands_off_the_rest() -> None:
    FakeDetector.plan = [1, 10_000]
    events: list = []

    async def scenario() -> str:
        s = _session(events)
        task = asyncio.ensure_future(s.run())
        await asyncio.sleep(0.1)
        s.control("do_it")
        return await task

    assert asyncio.run(scenario()) == "handoff"
    handoff = next(e for e in events if e["type"] == "guide_handoff")
    assert handoff["remaining"] == ["Click Printers", "Click Add"]
    run = next(e for e in events if e["type"] == "run_request")
    assert "add a printer" in run["goal"] and "Click Printers" in run["goal"]


def test_global_stop_and_timeout_pause() -> None:
    FakeDetector.plan = [10_000]
    events: list = []

    async def stop_soon() -> str:
        s = _session(events)
        task = asyncio.ensure_future(s.run())
        await asyncio.sleep(0.05)
        stop_ctl.trigger("test")
        return await task

    assert asyncio.run(stop_soon()) == "stopped"
    stop_ctl.reset()

    FakeDetector.plan = [10_000, 1, 1]

    async def paused() -> tuple:
        s = _session(events, step_timeout=0.05)
        task = asyncio.ensure_future(s.run())
        await asyncio.sleep(0.2)
        state = s.status
        s.control("skip")
        return state, await task

    assert asyncio.run(paused()) == ("paused", "done")


def test_guide_endpoints(sidecar_client, monkeypatch) -> None:  # noqa: ANN001
    from aether.core.config import Config
    from sidecar import guide_api

    monkeypatch.setattr(Config, "has_cloud_llm", lambda self: True)
    monkeypatch.setattr(guide_api.talk_api, "_client", lambda cfg: object())
    monkeypatch.setattr(guide_api, "_screen_summary", lambda redact: ("screen", "notes"))
    monkeypatch.setattr(guide_api.plan, "plan_steps",
                        lambda goal, client, **k: [GuideStep("Click Add", target="Add")])
    monkeypatch.setattr(guide_api.ClickWatcher, "start", lambda self: False)

    async def never_done(self) -> str:  # noqa: ANN001
        self.status = "running"
        await asyncio.sleep(3600)
        return "done"

    monkeypatch.setattr(GuideSession, "run", never_done)
    data = sidecar_client.post("/guide", json={"goal": "show me how to add a printer"}).json()
    assert data["goal"] == "add a printer" and data["total"] == 1
    gid = data["guide_id"]
    assert sidecar_client.get(f"/guide/{gid}").json()["status"] == "running"
    assert sidecar_client.post(f"/guide/{gid}", json={"action": "fly"}).status_code == 400
    assert sidecar_client.post(f"/guide/{gid}", json={"action": "next"}).status_code == 200
    assert sidecar_client.get("/guide/nope").status_code == 404
    monkeypatch.setattr(guide_api.plan, "plan_steps", lambda goal, client, **k: [])
    assert sidecar_client.post("/guide", json={"goal": "do a backflip"}).status_code == 422
