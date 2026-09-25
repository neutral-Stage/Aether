"""The targeting ladder: click by name, by visible text, by numbered mark.

Pixel coordinates drift; names don't. The ladder, best first (cursor-voice):

1. ``click_element(name)``: a fresh accessibility tree at execution time,
   scored name matching, AXPress, then a center click.
2. ``click(element_index, label=)``: an index from get_screen_context, with a
   label guard that refuses when the index no longer shows that label.
3. ``click_text(text)``: on-device OCR of a fresh capture, clicking the box of
   the matched word (not the whole line).
4. ``mark_screen`` then ``click_mark(n)``: numbered boxes drawn on a screenshot
   the model sees, for canvas/Electron apps with thin accessibility trees.
5. ``click_described(description)``: a vision model (the optional local
   grounder, else the vision role) finds the described thing on a fresh
   screenshot, refines on a close crop and snaps to the element under it.
6. ``click(x, y, space="image")``: raw coordinates read off the last attached
   screenshot, mapped through its display geometry and the calibrated
   coordinate convention.

Name-based tools refuse when a fuzzy match lands on a control the policy
would confirm but the requested name would not have ("Trash" resolving to
"Empty Trash"), so the confirmation always names what is pressed.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..effectors import executor, targeting
from ..effectors import input as kbd
from ..perception import accessibility as ax
from ..perception import ocr, screen

if TYPE_CHECKING:
    from .registry import AgentContext, ToolSpec

MAX_MARKS = 60
_MIN_SIDE_PT = 4.0
_SEGMENT_SPLIT_RE = re.compile(r"\s{2,}|\s*[|•·]\s*")


def model_edge() -> int | None:
    """Long-edge cap for images the model sees (grounding calibration)."""
    try:
        return int(screen.grounding_settings().get("max_image_edge") or 0) or None
    except Exception:  # noqa: BLE001
        return None


def attach_image(ctx: "AgentContext", path: str) -> str:
    """Queue an image for the model (sized to the calibrated edge); returns its path."""
    edge = model_edge()
    model_path = screen.resized_copy(path, edge) if edge else path
    ctx.pending_images.append(model_path)
    ctx.last_model_image = model_path
    return model_path


def _click_point(x: float, y: float, args: dict) -> None:
    with executor.HID_LOCK:
        kbd.click(x, y, button=str(args.get("button") or "left"),
                  count=2 if args.get("double") else 1)
    time.sleep(0.3)


# ---- click_element -------------------------------------------------------------

def _h_click_element(args: dict, _ctx: "AgentContext") -> str:
    name = str(args.get("name") or "").strip()
    if not name:
        return "ERROR: name is required."
    try:
        out = targeting.click_element(
            name, role=(args.get("role") or None), app=(args.get("app") or None),
            double=bool(args.get("double")), button=str(args.get("button") or "left"))
    except RuntimeError as e:
        return f"ERROR: {e}"
    if not out.startswith("ERROR"):
        time.sleep(0.3)
    return out


# ---- click_text ----------------------------------------------------------------

def control_segment(line: str, start: int) -> str:
    """The part of an OCR line around a match, split at wide gaps and bullets.

    OCR can merge neighbouring buttons into one line ("Send Feedback   Help");
    the sensitivity guard should judge the control that was hit, not its
    neighbours.
    """
    pos = 0
    for part in _SEGMENT_SPLIT_RE.split(line):
        idx = line.find(part, pos)
        if idx <= start < idx + len(part):
            return part.strip()
        pos = max(pos, idx + len(part))
    return line.strip()


def _h_click_text(args: dict, _ctx: "AgentContext") -> str:
    text = str(args.get("text") or "").strip()
    if not text:
        return "ERROR: text is required."
    if not ocr.available():
        return "ERROR: OCR unavailable (pyobjc-framework-Vision missing). Use mark_screen."
    occurrence = max(1, int(args.get("occurrence") or 1))
    try:
        cap = screen.capture()
    except Exception as e:  # noqa: BLE001
        return f"ERROR: screen capture failed ({e}). Is Screen Recording allowed for Aether?"
    hits = ocr.find_text(cap.path, text)
    if not hits:
        return (f"ERROR: '{text}' is not on screen (OCR). Scroll, try a shorter part of the "
                "text, or use mark_screen.")
    if occurrence > len(hits):
        return f"ERROR: only {len(hits)} occurrence(s) of '{text}' on screen."
    hit = hits[occurrence - 1]
    guard = targeting.sensitivity_guard(text, control_segment(hit.line, hit.start),
                                        "click_text", "text")
    if guard:
        return guard
    pts = ocr.regions_to_points([hit.region], cap.path)
    if not pts:
        return "ERROR: could not map the OCR box to screen points."
    r = pts[0]
    cx, cy = r.x + r.w / 2.0, r.y + r.h / 2.0
    _click_point(cx, cy, args)
    more = (f" {len(hits)} matches on screen; pass occurrence=2… for the others."
            if len(hits) > 1 else "")
    return f"Clicked '{hit.match}' in \"{hit.line[:60]}\" at ({int(cx)}, {int(cy)}).{more}"


# ---- mark_screen / click_mark --------------------------------------------------

@dataclass
class Mark:
    n: int
    x: float        # box in global screen points
    y: float
    w: float
    h: float
    label: str
    kind: str       # AX role without the prefix, or "text"

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)


def _inside(px: float, py: float, box: tuple[float, float, float, float]) -> bool:
    x, y, w, h = box
    return x <= px <= x + w and y <= py <= y + h


def plan_marks(elements: list, regions: list, bounds: tuple[float, float, float, float] | None,
               max_marks: int = MAX_MARKS) -> list[Mark]:
    """Pick and number targets: actionable AX elements first, then OCR text
    that no AX element already covers. Numbered in reading order."""
    area = (bounds[2] * bounds[3]) if bounds else 0.0
    picked: list[tuple[float, float, float, float, str, str, int]] = []
    seen_centers: set[tuple[int, int]] = set()

    def ok_box(x: float, y: float, w: float, h: float) -> bool:
        if w < _MIN_SIDE_PT or h < _MIN_SIDE_PT:
            return False
        if area and w * h > 0.5 * area:
            return False
        return not bounds or _inside(x + w / 2.0, y + h / 2.0, bounds)

    ax_candidates = []
    for e in elements:
        if not getattr(e, "enabled", True):
            continue
        label = targeting.label_of(e)
        actionable = e.role in targeting._ACTIONABLE  # noqa: SLF001
        if not (actionable or (e.title or "").strip()):
            continue
        if not ok_box(e.x, e.y, e.w, e.h):
            continue
        ax_candidates.append((0 if actionable else 1, e.w * e.h, e, label))
    ax_candidates.sort(key=lambda t: (t[0], t[1]))
    for prio, _, e, label in ax_candidates:
        key = (int((e.x + e.w / 2.0) // 4), int((e.y + e.h / 2.0) // 4))
        if key in seen_centers:
            continue
        seen_centers.add(key)
        picked.append((e.x, e.y, e.w, e.h, label, e.role.removeprefix("AX"), prio))

    ax_boxes = [(p[0], p[1], p[2], p[3]) for p in picked]
    for r in regions:
        if not (r.text or "").strip() or not ok_box(r.x, r.y, r.w, r.h):
            continue
        cx, cy = r.x + r.w / 2.0, r.y + r.h / 2.0
        if any(_inside(cx, cy, b) for b in ax_boxes):
            continue
        picked.append((r.x, r.y, r.w, r.h, r.text.strip(), "text", 2))

    picked.sort(key=lambda p: p[6])
    picked = picked[:max(1, max_marks)]
    picked.sort(key=lambda p: (round(p[1] / 12.0), p[0]))
    return [Mark(i + 1, x, y, w, h, label, kind)
            for i, (x, y, w, h, label, kind, _prio) in enumerate(picked)]


_MARK_COLORS = ("#E5243B", "#1F6FEB", "#1A7F37", "#8250DF", "#BF3989", "#9A6700")


def draw_marks(cap: "screen.Capture", marks: list[Mark], max_edge: int | None) -> str:
    """Draw numbered boxes on a copy of the capture; returns the registered PNG."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(cap.path).convert("RGB")
    scale = 1.0
    if max_edge and max(img.size) > max_edge:
        scale = max_edge / float(max(img.size))
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                         Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(img)
    size = max(11, min(22, round(img.width / 90)))
    font = ImageFont.load_default(size=size)
    for m in marks:
        (px0, py0) = cap.to_pixels(m.x, m.y)
        (px1, py1) = cap.to_pixels(m.x + m.w, m.y + m.h)
        box = (px0 * scale, py0 * scale, px1 * scale, py1 * scale)
        color = _MARK_COLORS[m.n % len(_MARK_COLORS)]
        draw.rectangle(box, outline=color, width=2)
        text = str(m.n)
        tl, tt, tr, tb = draw.textbbox((0, 0), text, font=font)
        tw, th = tr - tl + 6, tb - tt + 4
        bx = min(max(0.0, box[0]), img.width - tw)
        by = box[1] - th if box[1] - th >= 0 else box[1]
        draw.rectangle((bx, by, bx + tw, by + th), fill=color)
        draw.text((bx + 3 - tl, by + 2 - tt), text, fill="white", font=font)
    out = screen._temp_png("aether-marks-")  # noqa: SLF001
    img.save(out, format="PNG")
    screen.register_derived(cap, out, img.width, img.height)
    return out


def _h_mark_screen(args: dict, ctx: "AgentContext") -> str:
    try:
        cap = screen.capture()
    except Exception as e:  # noqa: BLE001
        return f"ERROR: screen capture failed ({e}). Is Screen Recording allowed for Aether?"
    ctx.last_screenshot = cap.path
    elements = ax.read_tree(max_elements=300)
    regions: list = []
    if args.get("include_text", True) and ocr.available():
        regions = ocr.regions_to_points(ocr.recognize_text(cap.path), cap.path) or []
    d = cap.display
    bounds = (d.x, d.y, d.width, d.height) if d.known and d.width > 0 else None
    limit = max(5, min(int(args.get("max_marks") or MAX_MARKS), 120))
    marks = plan_marks(elements, regions, bounds, limit)
    if not marks:
        return ("ERROR: found nothing to mark on this display. Use screenshot and "
                "click(x, y, space='image') as the last resort.")
    ctx.marks = {m.n: {"x": m.center[0], "y": m.center[1], "label": m.label, "kind": m.kind}
                 for m in marks}
    try:
        img = draw_marks(cap, marks, model_edge())
        ctx.pending_images.append(img)
        ctx.last_model_image = img
        attached = "on the attached screenshot"
    except ImportError:
        attached = "(Pillow is not installed, so no image; use the list)"
    listing = "\n".join(f"[{m.n}] {m.label[:50]!r} ({m.kind})" for m in marks)
    return (f"Numbered {len(marks)} targets {attached}. Call click_mark(mark=n). "
            f"Marks go stale when the screen changes; mark again after it does.\n{listing}")


def _h_click_mark(args: dict, ctx: "AgentContext") -> str:
    try:
        n = int(args.get("mark"))
    except (TypeError, ValueError):
        return "ERROR: mark must be a number from mark_screen."
    m = (ctx.marks or {}).get(n)
    if m is None:
        return f"ERROR: no mark {n}. Call mark_screen first."
    _click_point(float(m["x"]), float(m["y"]), args)
    return f"Clicked mark {n} '{str(m.get('label', ''))[:60]}' at ({int(m['x'])}, {int(m['y'])})."


def _h_click_described(args: dict, ctx: "AgentContext") -> str:
    desc = " ".join(str(args.get("description") or "").split())[:200]
    if not desc:
        return "ERROR: description is required."
    if ctx.locate is None:
        return "ERROR: click_described is only available inside an agent run."
    try:
        found = ctx.locate(desc)
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not look at the screen ({e}). Is Screen Recording allowed?"
    if found is None:
        return (f"ERROR: the vision model could not find '{desc}' on screen. Describe it "
                "differently (position, colour, nearby text) or use mark_screen.")
    label = found.element_label
    if label:
        refusal = targeting.sensitivity_guard(desc, label, "click_described", "description")
        if refusal:
            return refusal
    _click_point(found.x, found.y, args)
    by = "the local grounder" if found.source == "grounder" else "the vision model"
    on = f" on '{label[:60]}'" if label else ""
    return f"Clicked{on} at ({int(found.x)}, {int(found.y)}), found by {by}."


def _h_point_at(_args: dict, _ctx: "AgentContext") -> str:
    # Pointing needs the app's overlay: the agent loop resolves the target and
    # emits a pointer event instead of calling this.
    return "ERROR: point_at is only available inside an agent run."


# ---- image coordinates ---------------------------------------------------------

def image_point_to_screen(ctx: "AgentContext", x: float, y: float) -> tuple[float, float]:
    """Coordinates read off the last attached screenshot → global screen points."""
    from ..perception import grounding

    cap = screen.capture_info(ctx.last_model_image)
    if cap is None:
        raise ValueError("no screenshot to read image coordinates from — call screenshot "
                         "or mark_screen first.")
    space = grounding.resolve_coord_space(screen.grounding_settings().get("coord_space"))
    return grounding.to_screen_point(cap, float(x), float(y), space)


# ---- registration ----------------------------------------------------------------

def describe(name: str, args: dict) -> str | None:
    if name == "click_element":
        role = f" ({args['role']})" if args.get("role") else ""
        app = f" in {args['app']}" if args.get("app") else ""
        return f"click '{str(args.get('name', ''))[:50]}'{role}{app}"
    if name == "click_text":
        return f"click text '{str(args.get('text', ''))[:50]}'"
    if name == "mark_screen":
        return "number the targets on screen"
    if name == "click_mark":
        return f"click mark {args.get('mark')}"
    if name == "click_described":
        return f"click {str(args.get('description', ''))[:60]}"
    if name == "point_at":
        what = (args.get("label") or args.get("name") or args.get("description")
                or args.get("element_index") or "a spot")
        return f"point at {str(what)[:50]}"
    return None


def resolve_point_target(args: dict, ctx: "AgentContext") -> tuple[float, float, float, float, str]:
    """(x, y, w, h, label) in screen points for point_at; raises ValueError."""
    label = str(args.get("label") or args.get("name") or "")
    name = str(args.get("name") or "").strip()
    if name:
        match, _, _ = targeting.find(name, args.get("role") or None, args.get("app") or None)
        if match is None:
            raise ValueError(f"no element named '{name}' on screen")
        el = match.element
        return (el.x + el.w / 2.0, el.y + el.h / 2.0, float(el.w), float(el.h),
                label or targeting.label_of(el))
    idx = args.get("element_index")
    if idx is not None:
        for el in ctx.elements or []:
            if el.get("idx") == int(idx):
                return (el["x"] + el["w"] / 2.0, el["y"] + el["h"] / 2.0, float(el["w"]),
                        float(el["h"]), label or str(el.get("title") or ""))
        raise ValueError(f"element {idx} not found; call get_screen_context again")
    desc = " ".join(str(args.get("description") or "").split())[:200]
    if desc:
        if ctx.locate is None:
            raise ValueError("finding things by description needs an agent run")
        found = ctx.locate(desc)
        if found is None:
            raise ValueError(f"could not find '{desc}' on screen")
        el = found.element
        return (found.x, found.y, float(getattr(el, "w", 0) or 0),
                float(getattr(el, "h", 0) or 0), label or found.element_label or desc)
    if args.get("x") is not None and args.get("y") is not None:
        if str(args.get("space") or "screen") == "image":
            x, y = image_point_to_screen(ctx, args["x"], args["y"])
        else:
            x, y = float(args["x"]), float(args["y"])
        return (x, y, 0.0, 0.0, label)
    raise ValueError("point_at needs name, element_index, description, or x and y")


def specs() -> list["ToolSpec"]:
    from .registry import ToolSpec

    click_opts = {"button": {"type": "string", "enum": ["left", "right"]},
                  "double": {"type": "boolean"}}
    return [
        ToolSpec(
            name="click_element",
            description=("Click a control by its NAME, e.g. name='Save', role='button'. Best "
                         "way to click: reads the live accessibility tree at click time, so it "
                         "never hits a stale index. Pass app='Name' for a background app."),
            json_schema={"type": "object", "properties": {
                "name": {"type": "string", "description": "visible label, title or tooltip"},
                "role": {"type": "string",
                         "description": "button, link, field, checkbox, tab, menu item, …"},
                "app": {"type": "string"}, **click_opts}, "required": ["name"]},
            permission="input", impact="reversible", handler=_h_click_element),
        ToolSpec(
            name="click_text",
            description=("Click visible TEXT found by on-device OCR (for apps whose controls "
                         "have no accessibility names). occurrence=2 picks the second match."),
            json_schema={"type": "object", "properties": {
                "text": {"type": "string"}, "occurrence": {"type": "integer"}, **click_opts},
                "required": ["text"]},
            permission="input", impact="reversible", handler=_h_click_text),
        ToolSpec(
            name="mark_screen",
            description=("Attach a screenshot with numbered boxes on clickable things "
                         "(accessibility elements plus OCR text), then click_mark(n). Use when "
                         "click_element and click_text can't find the target."),
            json_schema={"type": "object", "properties": {
                "include_text": {"type": "boolean", "description": "also mark OCR text (default true)"},
                "max_marks": {"type": "integer"}}},
            permission="screen", impact="read", handler=_h_mark_screen),
        ToolSpec(
            name="point_at",
            description=("Show the user where something is: the on-screen pointer flies to it "
                         "and circles it. Use when explaining or teaching, not to act. Pass "
                         "name (best), element_index, a description a vision model can "
                         "find, or x,y (space='image' for screenshot pixels), plus a short "
                         "label."),
            json_schema={"type": "object", "properties": {
                "name": {"type": "string"}, "role": {"type": "string"},
                "description": {"type": "string"},
                "element_index": {"type": "integer"}, "x": {"type": "number"},
                "y": {"type": "number"}, "space": {"type": "string", "enum": ["screen", "image"]},
                "label": {"type": "string"}}},
            permission="screen", impact="read", handler=_h_point_at),
        ToolSpec(
            name="click_described",
            description=("Click something you can describe but not name, e.g. 'the gear icon "
                         "right of Wi-Fi' or 'the blue Export button at the bottom'. A vision "
                         "model finds it on a fresh screenshot. Use after click_element and "
                         "click_text fail; include any visible text in the description."),
            json_schema={"type": "object", "properties": {
                "description": {"type": "string"}, **click_opts}, "required": ["description"]},
            permission="input", impact="reversible", handler=_h_click_described),
        ToolSpec(
            name="click_mark",
            description="Click a numbered target from the last mark_screen.",
            json_schema={"type": "object", "properties": {
                "mark": {"type": "integer"}, **click_opts}, "required": ["mark"]},
            permission="input", impact="reversible", handler=_h_click_mark),
    ]
