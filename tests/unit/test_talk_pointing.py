"""Talk mode: pointing tags, resolution and snapping, the scene, /talk, point_at."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import pytest

from aether import talk
from aether.core import llm
from aether.core.config import Config
from aether.core.llm import LLMResponse
from aether.perception import accessibility as ax
from aether.perception import pointing as pt
from aether.perception import screen


def el(idx: int, title: str, x: float, y: float, w: float = 80, h: float = 24,
       role: str = "AXButton") -> ax.Element:
    return ax.Element(idx, role, title, "", True, x, y, w, h)


def cap_for(path: str, w: int = 1440, h: int = 900, x0: float = 0.0) -> screen.Capture:
    disp = screen.DisplayInfo(1, 1, x0, 0.0, 1440.0, 900.0, 2.0, True, True)
    return screen._register(screen.Capture(path, disp, w, h, 1, True, "native"))  # noqa: SLF001


# ---- tags -------------------------------------------------------------------------------------

def test_parse_and_strip_all_tag_kinds() -> None:
    text = ("Turn it on here. [POINT:e12:Bluetooth] [POINT:640,212:Save:screen2] "
            "[RECT:1,2,30,40:box] [SCRIBBLE:10,10;40,12;80,30:this] [POINT:none]")
    tags = pt.parse_tags(text)
    assert [t.kind for t in tags] == ["point", "point", "rect", "scribble", "none"]
    assert tags[0].element_id == 12 and tags[0].label == "Bluetooth"
    assert (tags[1].x, tags[1].y, tags[1].screen) == (640.0, 212.0, 2)
    assert tags[3].points == [(10.0, 10.0), (40.0, 12.0), (80.0, 30.0)]
    assert pt.strip_tags(text) == "Turn it on here."


def test_stream_parser_never_speaks_tags() -> None:
    p = pt.TagStreamParser()
    spoken, tags = [], []
    for chunk in ["Open the ", "menu [PO", "INT:e3:File", "] then ", "click [RE"]:
        s, t = p.feed(chunk)
        spoken.append(s)
        tags += t
    s, t = p.close()
    spoken.append(s)
    assert "".join(spoken) == "Open the menu  then click "
    assert [x.element_id for x in tags] == [3]
    p2 = pt.TagStreamParser()
    assert p2.feed("Array[0] is fine")[0] == "Array[0] is fine"   # not a tag start


# ---- resolution ---------------------------------------------------------------------------------

def test_resolve_element_pixel_rect_and_scribble(tmp_path) -> None:  # noqa: ANN001
    cap = cap_for(str(tmp_path / "s.png"))         # 1 px per point
    ctx = pt.PointContext(captures=[cap], elements={3: el(3, "Save", 100, 100)},
                          snap_elements=[el(9, "OK", 500, 500, 40, 20)])
    a = pt.resolve(pt.parse_tags("[POINT:e3]")[0], ctx)
    assert (a.x, a.y, a.source, a.label) == (140.0, 112.0, "ax", "Save")
    b = pt.resolve(pt.parse_tags("[POINT:505,530:ok]")[0], ctx)   # 10 pt below OK → snaps
    assert (b.x, b.y, b.source) == (520.0, 510.0, "snap")
    c = pt.resolve(pt.parse_tags("[POINT:5000,-20:off]")[0], ctx)  # clamped to the display
    assert (c.x, c.y, c.source) == (1439.0, 0.0, "image")
    r = pt.resolve(pt.parse_tags("[RECT:10,20,100,50:area]")[0], ctx)
    assert (r.kind, r.x, r.y, r.w, r.h) == ("rect", 60.0, 45.0, 100.0, 50.0)
    s = pt.resolve(pt.parse_tags("[SCRIBBLE:1,1;9,9:x]")[0], ctx)
    assert s.points == [(1.0, 1.0), (9.0, 9.0)]
    assert pt.resolve(pt.parse_tags("[POINT:e99]")[0], ctx) is None
    assert pt.resolve(pt.parse_tags("[POINT:none]")[0], ctx) is None


def test_resolve_retina_and_grid(tmp_path) -> None:  # noqa: ANN001
    cap = cap_for(str(tmp_path / "r.png"), 2880, 1800, x0=1440.0)   # 2 px/pt, second display
    ctx = pt.PointContext(captures=[cap], coord_space="pixels")
    p = pt.resolve(pt.parse_tags("[POINT:200,100:x]")[0], ctx)
    assert (p.x, p.y) == (1540.0, 50.0)
    ctx.coord_space = "norm1000"
    q = pt.resolve(pt.parse_tags("[POINT:500,500:x]")[0], ctx)
    assert (q.x, q.y) == (2160.0, 450.0)


def test_snap_prefers_containing_then_nearest_smallest() -> None:
    big = el(1, "Panel", 0, 0, 600, 400)
    small = el(2, "Button", 100, 100, 40, 20)
    near = el(3, "Near", 200, 100, 40, 20)
    assert pt.snap(110, 110, [big, small]) is small
    assert pt.snap(190, 110, [near]) is near          # 10 pt away
    assert pt.snap(150, 300, [small, near]) is None     # too far from both


# ---- under the cursor and crops ------------------------------------------------------------------

def test_drill_to_smallest_child() -> None:
    frames = {"root": (0, 0, 500, 500), "a": (0, 0, 200, 200), "b": (10, 10, 50, 50),
              "c": (300, 300, 50, 50)}
    kids = {"root": ["a", "c"], "a": ["b"], "b": []}
    got = ax.drill_to_smallest("root", 20, 20, children_of=lambda h: kids.get(h, []),
                               frame_of=frames.get)
    assert got == "b"
    assert ax.drill_to_smallest("root", 400, 100, children_of=lambda h: kids.get(h, []),
                                frame_of=frames.get) == "root"


def test_crop_maps_back_to_points(tmp_path) -> None:  # noqa: ANN001
    from PIL import Image

    path = str(tmp_path / "full.png")
    Image.new("RGB", (2880, 1800), "white").save(path)
    cap = cap_for(path, 2880, 1800)
    crop = screen.crop_around(cap, 720, 450, half_pt=100, scale=2.0)
    assert (crop.width, crop.height) == (800, 800)
    assert crop.to_points(400, 400) == (720.0, 450.0)
    edge = screen.crop_around(cap, 5, 5, half_pt=100, scale=1.0)
    assert (edge.px0, edge.py0) == (0.0, 0.0) and edge.to_points(10, 10) == (5.0, 5.0)


def test_openai_user_images_become_one_multimodal_message(tmp_path) -> None:  # noqa: ANN001
    from PIL import Image

    img = tmp_path / "a.png"
    Image.new("RGB", (4, 4)).save(img)
    msgs = [{"role": "user", "content": [{"type": "text", "text": "look"},
                                          llm.image_block(str(img)),
                                          {"type": "text", "text": "what is it?"}]}]
    oai = llm._anthropic_messages_to_openai("sys", msgs)  # noqa: SLF001
    parts = oai[-1]["content"]
    assert [p["type"] for p in parts] == ["text", "image_url", "text"]
    text_only = llm._anthropic_messages_to_openai("sys", msgs, images=False)  # noqa: SLF001
    assert "image omitted" in str(text_only[-1]["content"])


# ---- the scene and asking ------------------------------------------------------------------------

def test_number_elements_nearest_first_in_reading_order(tmp_path) -> None:  # noqa: ANN001
    cap = cap_for(str(tmp_path / "n.png"))
    elements = [el(i, f"b{i}", 20 * i, 10 * (i % 3), 10, 10) for i in range(1, 40)]
    elements.append(el(99, "", 5, 5))                        # unlabelled
    elements.append(el(98, "off", 3000, 5))                  # other display
    numbered = talk.number_elements(elements, cap, (0, 0), limit=5)
    assert len(numbered) == 5 and all(e.title for e in numbered.values())
    assert list(numbered) == [1, 2, 3, 4, 5]
    assert "e1 Button 'b" in talk.describe_elements(numbered, cap)


def test_describe_under_cursor() -> None:
    info = {"element": {"role": "AXButton", "title": "Share", "value": "", "help": "Share this"},
            "ancestry": ["Toolbar 'Main'", "Group 'Top'"], "window": "Doc", "app": "Pages",
            "url": ""}
    text = talk.describe_under_cursor(info, "hello world")
    assert "Button 'Share'" in text and "tooltip 'Share this'" in text
    assert "Group 'Top' › Toolbar 'Main'" in text and "Selected text: hello world" in text
    assert "nothing readable" in talk.describe_under_cursor(None, "")


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


def test_scene_content_and_context(scene) -> None:  # noqa: ANN001
    kinds = [b["type"] for b in scene.content]
    assert kinds == ["text", "text", "text", "image", "text", "image"]
    assert "e1 Button 'Bluetooth'" in scene.content[1]["text"]
    assert scene.point_ctx.elements[1].title == "Bluetooth"


def test_ask_answers_points_and_keeps_history(scene) -> None:  # noqa: ANN001
    client = FakeClient(["Click this switch. [POINT:e1:Bluetooth]", "The one below. [POINT:e2]"])
    reply = talk.ask("where is bluetooth?", client, scene=scene, session_id="t1")
    assert reply.answer == "Click this switch."
    assert [(t.x, t.y, t.source) for t in reply.targets] == [(140.0, 112.0, "ax")]
    second = talk.ask("and wifi?", client, scene=scene, session_id="t1")
    msgs = client.calls[1][1]
    assert msgs[0] == {"role": "user", "content": "where is bluetooth?"}
    assert msgs[1] == {"role": "assistant", "content": "Click this switch."}
    assert second.targets[0].label == "Wi-Fi"


def test_pixel_points_are_refined_on_a_crop(scene) -> None:  # noqa: ANN001
    client = FakeClient(["It's there. [POINT:700,400:the logo]", '{"x": 160, "y": 140}'])
    reply = talk.ask("where's the logo?", client, scene=scene, session_id="t2")
    t = reply.targets[0]
    # crop origin = (700-150, 400-150) → (160,140) in the crop = (710, 390)
    assert (t.x, t.y, t.source) == (710.0, 390.0, "refined") and reply.refined == 1
    far = FakeClient(["[POINT:700,400:x]", '{"x": 0, "y": 0}'])      # a jump >160 pt is ignored
    assert talk.ask("?", far, scene=scene).targets[0].source == "image"
    miss = FakeClient(["[POINT:700,400:x]", '{"x": null}'])
    assert talk.ask("?", miss, scene=scene).targets[0].source == "image"


def test_talk_endpoint(sidecar_client, monkeypatch, scene) -> None:  # noqa: ANN001
    from sidecar import talk_api

    monkeypatch.setattr(Config, "has_cloud_llm", lambda self: True)
    monkeypatch.setattr(talk_api, "_client",
                        lambda cfg: FakeClient(["Here. [POINT:e1:Bluetooth]"]))
    monkeypatch.setattr(talk, "build_scene", lambda cursor, redact=None: scene)
    sent: list = []

    async def capture(ev):  # noqa: ANN001, ANN202
        sent.append(ev)

    monkeypatch.setattr(talk_api, "_broadcast", capture)
    data = sidecar_client.post("/talk", json={"question": "where is bluetooth", "x": 110,
                                              "y": 110}).json()
    assert data["answer"] == "Here." and data["targets"][0]["label"] == "Bluetooth"
    assert sent and sent[0]["type"] == "pointer"
    assert sidecar_client.post("/talk", json={"question": "  "}).status_code == 400


# ---- point_at ------------------------------------------------------------------------------------

def test_point_at_emits_pointer_event(minimal_config, monkeypatch) -> None:  # noqa: ANN001
    from aether.core.orchestrator import Agent
    from aether.core.router import RouteDecision, RouteTier
    from aether.effectors import targeting

    agent = Agent(minimal_config, hud=None)
    monkeypatch.setattr(agent.world, "refresh", lambda force=False: {})
    monkeypatch.setattr(agent, "say", lambda text: None)
    monkeypatch.setattr(agent.router, "route",
                        lambda *a, **k: RouteDecision(RouteTier.CLOUD_FRONTIER, "t"))
    monkeypatch.setattr(targeting, "find", lambda name, role=None, app=None: (
        NS(element=el(1, "Bluetooth", 100, 100)), [], "System Settings"))
    events: list = []
    agent.emit = events.append
    calls = iter([("point_at", {"name": "Bluetooth"}), ("point_at", {}),
                  ("finish", {"message": "ok"})])

    class Client:
        def step(self, system, messages, tools, *, abort_event=None):  # noqa: ANN001
            name, args = next(calls)
            call = {"id": name + str(len(messages)), "name": name, "input": args}
            return LLMResponse(text="", tool_calls=[call],
                               raw_content=[{"type": "tool_use", **call}],
                               stop_reason="tool_use", backend="fake")

    monkeypatch.setattr(agent.router, "pick_client", lambda d: Client())
    asyncio.run(agent.run_async("show me bluetooth", run_id="p"))
    ptr = [e for e in events if e["type"] == "pointer"]
    assert len(ptr) == 1 and ptr[0]["targets"][0]["x"] == 140.0
    assert ptr[0]["targets"][0]["label"] == "Bluetooth"
