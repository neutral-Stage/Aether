"""Targeting ladder: click by name, OCR word boxes, numbered marks, label guards."""
from __future__ import annotations

import pytest

from aether.effectors import ax_actions, targeting
from aether.perception import accessibility as ax
from aether.perception import ocr, screen
from aether.perception.ocr import TextHit, TextRegion
from aether.tools import targeting_tools as tt
from aether.tools.registry import DEFAULT_REGISTRY, AgentContext


def el(idx: int, role: str, title: str, *, value: str = "", enabled: bool = True,
       x: float = 0, y: float = 0, w: float = 80, h: float = 24, identifier: str = "",
       help: str = "") -> ax.Element:  # noqa: A002
    return ax.Element(idx, role, title, value, enabled, x, y, w, h, identifier, help)


# ---- scoring -------------------------------------------------------------------

def test_score_ladder() -> None:
    q = "Save"
    exact = targeting.score(el(1, "AXButton", "Save"), q)
    prefix = targeting.score(el(2, "AXButton", "Save As…"), q)
    contains = targeting.score(el(3, "AXButton", "Autosave"), q)
    value = targeting.score(el(4, "AXTextField", "", value="save"), q)
    ident = targeting.score(el(5, "AXButton", "", identifier="toolbar.save"), q)
    fuzzy = targeting.score(el(6, "AXButton", "Saev"), q)
    assert exact > prefix > contains > value > ident > 0
    assert targeting.score(el(7, "AXButton", "Open"), q) == 0
    assert fuzzy == 0 or fuzzy < ident


def test_role_bonus_and_disabled_penalty() -> None:
    base = targeting.score(el(1, "AXButton", "OK"), "ok")
    assert targeting.score(el(1, "AXButton", "OK"), "ok", "button") == base + 50
    assert targeting.score(el(1, "AXButton", "OK", enabled=False), "ok") == base - 150
    assert targeting.score(el(1, "AXStaticText", "OK"), "ok", "button") == 0


@pytest.mark.parametrize(("role", "wanted", "ok"), [
    ("AXToolbarButton", "button", True), ("AXTextField", "field", True),
    ("AXCheckBox", "toggle", True), ("AXPopUpButton", "dropdown", True),
    ("AXButton", "link", False), ("AXMenuItem", "MenuItem", True),
])
def test_role_aliases(role: str, wanted: str, ok: bool) -> None:
    assert targeting.role_matches(role, wanted) is ok


def test_rank_prefers_smaller_on_ties() -> None:
    big = el(1, "AXButton", "Done", w=400, h=300)
    small = el(2, "AXButton", "Done", w=60, h=20, x=500)
    assert [e.idx for _, e in targeting.rank([big, small], "done")] == [2, 1]


@pytest.mark.parametrize(("have", "want", "ok"), [
    ("Save", "save", True), ("Save As…", "Save As", True), ("Cancel", "Delete", False),
    ("Send Message", "Send", True), ("", "", True),
])
def test_label_consistent(have: str, want: str, ok: bool) -> None:
    assert targeting.label_consistent(have, want) is ok


# ---- find / click_element --------------------------------------------------------

@pytest.fixture
def fake_tree(monkeypatch):  # noqa: ANN001, ANN201
    tree: list[ax.Element] = []
    handles = {}

    def read_tree(max_elements=250, capture_handles=False, pid=None, handles_out=None):  # noqa: ANN001, ANN202, ARG001
        if handles_out is not None:
            handles_out.update({e.idx: f"h{e.idx}" for e in tree})
        return list(tree)

    monkeypatch.setattr(ax, "read_tree", read_tree)
    monkeypatch.setattr(ax, "frontmost_app", lambda: {"pid": 42, "name": "Finder"})
    clicks: list = []
    from aether.effectors import input as kbd

    monkeypatch.setattr(kbd, "click", lambda *a, **k: clicks.append((a, k)))
    pressed: list = []
    monkeypatch.setattr(ax_actions, "press_handle",
                        lambda h, label="": pressed.append(h) or f"AXPress {label}")
    return tree, handles, clicks, pressed


def test_find_best_and_ambiguity(fake_tree) -> None:  # noqa: ANN001
    tree, *_ = fake_tree
    tree += [el(1, "AXButton", "OK", x=10), el(2, "AXButton", "OK", x=300),
             el(3, "AXButton", "Cancel")]
    match, cands, app = targeting.find("ok")
    assert match is not None and match.ambiguous and app == "Finder"
    assert match.handle == "h1"
    none, cands, _ = targeting.find("Quit")
    assert none is None


def test_click_element_prefers_axpress(fake_tree) -> None:  # noqa: ANN001
    tree, _, clicks, pressed = fake_tree
    tree.append(el(4, "AXButton", "Save", x=100, y=100))
    out = targeting.click_element("save")
    assert "AXPress" in out and pressed == ["h4"] and not clicks


def test_click_element_falls_back_to_center_click(fake_tree, monkeypatch) -> None:  # noqa: ANN001
    tree, _, clicks, _ = fake_tree

    def boom(h, label=""):  # noqa: ANN001, ANN202, ARG001
        raise RuntimeError("no AXPress")

    monkeypatch.setattr(ax_actions, "press_handle", boom)
    tree.append(el(4, "AXButton", "Save", x=100, y=100, w=80, h=20))
    out = targeting.click_element("save")
    assert "(140, 110)" in out and clicks[0][0][:2] == (140.0, 110.0)


def test_click_element_reports_closest_and_disabled(fake_tree) -> None:  # noqa: ANN001
    tree, *_ = fake_tree
    tree.append(el(1, "AXButton", "Export", enabled=False))
    assert "disabled" in targeting.click_element("Export")
    assert "no element named 'Quit'" in targeting.click_element("Quit")


def test_click_element_refuses_fuzzy_match_onto_sensitive_control(fake_tree) -> None:  # noqa: ANN001
    tree, _, clicks, pressed = fake_tree
    tree.append(el(1, "AXButton", "Empty Trash"))
    out = targeting.click_element("Trash")
    assert out.startswith("ERROR") and "name='Empty Trash'" in out
    assert not clicks and not pressed
    assert "AXPress" in targeting.click_element("Empty Trash")


def test_current_label_prefers_live_handle(monkeypatch) -> None:
    monkeypatch.setattr(ax_actions, "_element_handles", {3: "handle"})
    monkeypatch.setattr(ax, "handle_label", lambda h: "Delete" if h == "handle" else None)
    assert targeting.current_label(3, [{"idx": 3, "title": "Cancel"}]) == "Delete"
    monkeypatch.setattr(ax, "handle_label", lambda h: None)
    assert targeting.current_label(3, [{"idx": 3, "title": "Cancel"}]) == "Cancel"
    assert targeting.current_label(9, []) is None


# ---- click with label guard / image space -----------------------------------------

@pytest.fixture
def no_native(monkeypatch):  # noqa: ANN001, ANN201
    from aether.effectors import input as kbd
    from aether.tools import registry

    clicks: list = []
    monkeypatch.setattr(registry, "_try_native_effector", lambda tool, args: None)
    monkeypatch.setattr(ax_actions, "can_press", lambda i: False)
    monkeypatch.setattr(kbd, "click", lambda *a, **k: clicks.append((a, k)))
    monkeypatch.setattr(registry.time, "sleep", lambda s: None)
    monkeypatch.setattr(ax, "handle_label", lambda h: None)
    return clicks


def test_click_label_guard(no_native) -> None:  # noqa: ANN001
    ctx = AgentContext(elements=[{"idx": 3, "title": "Cancel", "x": 0, "y": 0, "w": 10, "h": 10}])
    out = DEFAULT_REGISTRY.dispatch("click", {"element_index": 3, "label": "Delete"}, ctx)
    assert out.startswith("ERROR") and "click_element(name='Delete')" in out
    assert not no_native
    DEFAULT_REGISTRY.dispatch("click", {"element_index": 3, "label": "cancel"}, ctx)
    assert no_native[0][0][:2] == (5.0, 5.0)


def _retina_capture(path: str, w: int = 2880, h: int = 1800) -> screen.Capture:
    disp = screen.DisplayInfo(2, 7, 1440.0, 0.0, 1440.0, 900.0, 2.0, False, True)
    return screen._register(screen.Capture(path, disp, w, h, 2, True, "native"))  # noqa: SLF001


def test_click_in_image_space_maps_to_points(no_native, monkeypatch, tmp_path) -> None:  # noqa: ANN001
    img = str(tmp_path / "shot.png")
    _retina_capture(img, 1440, 900)       # model copy: 1 px per point
    ctx = AgentContext(last_model_image=img)
    monkeypatch.setattr(screen, "grounding_settings", lambda: {"coord_space": "pixels"})
    DEFAULT_REGISTRY.dispatch("click", {"x": 100, "y": 50, "space": "image"}, ctx)
    assert no_native[-1][0][:2] == (1540.0, 50.0)
    monkeypatch.setattr(screen, "grounding_settings", lambda: {"coord_space": "norm1000"})
    DEFAULT_REGISTRY.dispatch("click", {"x": 500, "y": 500, "space": "image"}, ctx)
    assert no_native[-1][0][:2] == (2160.0, 450.0)
    out = DEFAULT_REGISTRY.dispatch("click", {"x": 1, "y": 1, "space": "image"}, AgentContext())
    assert "screenshot" in out and out.startswith("ERROR")


# ---- OCR word boxes ---------------------------------------------------------------

def test_find_in_lines_word_box_and_ranking() -> None:
    lines = [(TextRegion("Save  Cancel", 0.9, 0.1, 0.5, 0.24, 0.02), None),
             (TextRegion("Save", 0.9, 0.5, 0.8, 0.04, 0.02), None),
             (TextRegion("Autosave", 0.9, 0.1, 0.1, 0.08, 0.02), None)]
    hits = ocr.rank_hits(ocr.find_in_lines(lines, "save"), "save")
    assert [h.line for h in hits] == ["Save", "Save  Cancel", "Autosave"]
    word = hits[1].region
    assert word.x == pytest.approx(0.1) and word.w == pytest.approx(0.24 * 4 / 12)
    assert hits[2].start == 4


def test_find_in_lines_uses_vision_range_box() -> None:
    class Rect:
        def boundingBox(self):  # noqa: ANN202, N802
            from types import SimpleNamespace as NS

            return NS(origin=NS(x=0.30, y=0.40), size=NS(width=0.05, height=0.02))

    class Cand:
        def boundingBoxForRange_error_(self, rng, err):  # noqa: ANN001, ANN202, N802, ARG002
            assert rng == (6, 6)
            return (Rect(), None)

    hits = ocr.find_in_lines([(TextRegion("Click Export", 1, 0.2, 0.5, 0.3, 0.02), Cand())],
                             "export")
    r = hits[0].region
    assert (r.x, r.w) == (0.30, 0.05) and r.y == pytest.approx(1 - 0.40 - 0.02)


def test_find_in_lines_folded_match_uses_whole_line() -> None:
    hits = ocr.find_in_lines([(TextRegion("Export as   PDF…", 1, 0.1, 0.1, 0.2, 0.02), None)],
                             "export as pdf...")
    assert len(hits) == 1 and hits[0].region.w == 0.2


@pytest.mark.parametrize(("line", "start", "seg"), [
    ("Send Feedback   Help", 16, "Help"), ("Empty Trash", 6, "Empty Trash"),
    ("File | Edit | View", 7, "Edit"),
])
def test_control_segment(line: str, start: int, seg: str) -> None:
    assert tt.control_segment(line, start) == seg


@pytest.fixture
def ocr_screen(monkeypatch, tmp_path, no_native):  # noqa: ANN001, ANN201
    path = str(tmp_path / "cap.png")
    cap = _retina_capture(path)
    monkeypatch.setattr(screen, "capture", lambda *a, **k: cap)
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(tt.time, "sleep", lambda s: None)
    return cap


def test_click_text_clicks_word_center_in_points(ocr_screen, monkeypatch, no_native) -> None:  # noqa: ANN001
    hit = TextHit("Save", "Save", TextRegion("Save", 1, 0.5, 0.5, 0.1, 0.02), 0)
    monkeypatch.setattr(ocr, "find_text", lambda p, t: [hit, hit])
    out = DEFAULT_REGISTRY.dispatch("click_text", {"text": "Save"}, AgentContext())
    # 0.55 * 1440pt + 1440 origin, 0.51 * 900pt
    assert no_native[-1][0][:2] == (pytest.approx(2232.0), pytest.approx(459.0))
    assert "2 matches" in out
    assert "only 2" in DEFAULT_REGISTRY.dispatch("click_text", {"text": "Save", "occurrence": 3},
                                                  AgentContext())


def test_click_text_guard(ocr_screen, monkeypatch, no_native) -> None:  # noqa: ANN001
    hit = TextHit("Empty Trash", "Trash", TextRegion("Trash", 1, 0.5, 0.5, 0.1, 0.02), 6)
    monkeypatch.setattr(ocr, "find_text", lambda p, t: [hit])
    out = DEFAULT_REGISTRY.dispatch("click_text", {"text": "Trash"}, AgentContext())
    assert out.startswith("ERROR") and "text='Empty Trash'" in out and not no_native
    monkeypatch.setattr(ocr, "find_text", lambda p, t: [])
    assert "not on screen" in DEFAULT_REGISTRY.dispatch("click_text", {"text": "x"}, AgentContext())


# ---- marks --------------------------------------------------------------------------

def test_plan_marks_prefers_ax_and_dedupes_text() -> None:
    bounds = (0.0, 0.0, 1000.0, 800.0)
    elements = [el(1, "AXButton", "OK", x=500, y=400, w=60, h=20),
                el(2, "AXButton", "Cancel", x=400, y=400, w=60, h=20),
                el(3, "AXGroup", "", x=0, y=0, w=900, h=700),          # huge, unnamed
                el(4, "AXButton", "Off", x=2000, y=10),                 # other display
                el(5, "AXButton", "Gone", enabled=False, x=10, y=10)]
    regions = [TextRegion("OK", 1, 510, 402, 20, 14),                  # inside button 1
               TextRegion("Canvas label", 1, 100, 100, 90, 16)]
    marks = tt.plan_marks(elements, regions, bounds)
    assert [(m.n, m.label, m.kind) for m in marks] == [
        (1, "Canvas label", "text"), (2, "Cancel", "Button"), (3, "OK", "Button")]
    capped = tt.plan_marks(elements, regions, bounds, max_marks=2)
    assert {m.kind for m in capped} == {"Button"}


def test_draw_marks_scales_and_registers(tmp_path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    path = str(tmp_path / "full.png")
    Image.new("RGB", (2880, 1800), "white").save(path)
    cap = _retina_capture(path)
    marks = [tt.Mark(1, 1540.0, 100.0, 100.0, 50.0, "OK", "Button")]
    out = tt.draw_marks(cap, marks, 1440)
    img = Image.open(out)
    assert img.size == (1440, 900)
    info = screen.capture_info(out)
    assert info is not None and (info.pixel_width, info.pixel_height) == (1440, 900)
    # the box's left edge sits at (1540-1440) px on the 1 px/pt copy
    assert img.getpixel((100, 125)) != (255, 255, 255)
    assert info.to_points(100, 125) == (1540.0, 125.0)


def test_mark_screen_then_click_mark(ocr_screen, monkeypatch, no_native) -> None:  # noqa: ANN001
    monkeypatch.setattr(ax, "read_tree", lambda **k: [
        el(1, "AXButton", "Share", x=1500, y=100, w=60, h=20)])
    monkeypatch.setattr(ocr, "recognize_text", lambda p: [])
    monkeypatch.setattr(tt, "draw_marks", lambda cap, marks, edge: "/tmp/marks.png")
    ctx = AgentContext()
    out = DEFAULT_REGISTRY.dispatch("mark_screen", {}, ctx)
    assert "[1] 'Share' (Button)" in out
    assert ctx.pending_images == ["/tmp/marks.png"] and ctx.marks[1]["label"] == "Share"
    DEFAULT_REGISTRY.dispatch("click_mark", {"mark": 1}, ctx)
    assert no_native[-1][0][:2] == (1530.0, 110.0)
    assert "no mark 7" in DEFAULT_REGISTRY.dispatch("click_mark", {"mark": 7}, ctx)


def test_screenshot_tool_attaches_model_copy(monkeypatch, tmp_path) -> None:
    from aether.tools import registry

    full = str(tmp_path / "full.png")
    monkeypatch.setattr(registry.screen_cap, "capture_to_file", lambda *a, **k: full)
    monkeypatch.setattr(screen, "grounding_settings", lambda: {"max_image_edge": 1600})
    monkeypatch.setattr(screen, "resized_copy", lambda p, e: p + f".{e}.png")
    ctx = AgentContext()
    out = DEFAULT_REGISTRY.dispatch("screenshot", {}, ctx)
    assert "attached" in out
    assert ctx.pending_images == [full + ".1600.png"] == [ctx.last_model_image]
    assert ctx.last_screenshot == full


def test_targeting_tools_registered() -> None:
    for name, perm, impact in [("click_element", "input", "reversible"),
                               ("click_text", "input", "reversible"),
                               ("mark_screen", "screen", "read"),
                               ("click_mark", "input", "reversible")]:
        spec = DEFAULT_REGISTRY.get(name)
        assert spec is not None and (spec.permission, spec.impact) == (perm, impact)
    assert DEFAULT_REGISTRY.describe_call("click_element", {"name": "Save", "role": "button"}) \
        == "click 'Save' (button)"


def test_native_click_gets_resolved_point(monkeypatch) -> None:
    from aether.tools import registry

    sent: list = []
    monkeypatch.setattr(registry, "_try_native_effector",
                        lambda tool, args: sent.append(args) or "Clicked via Swift.")
    monkeypatch.setattr(ax_actions, "can_press", lambda i: False)
    monkeypatch.setattr(registry.time, "sleep", lambda s: None)
    ctx = AgentContext(elements=[{"idx": 3, "title": "OK", "x": 10, "y": 20, "w": 40, "h": 10}])
    assert "Swift" in DEFAULT_REGISTRY.dispatch("click", {"element_index": 3}, ctx)
    assert sent == [{"x": 30.0, "y": 25.0}]
