"""Talk mode: point at something on screen and ask about it.

One vision call, no agent loop. The model is shown:

- a labelled screenshot of the display under the mouse (Aether's own windows
  hidden),
- what is under the pointer: the element, its container path, window, app and
  page URL, plus any selected text,
- a numbered list of on-screen elements (``e1…eN``), so it can point at real
  controls instead of guessing pixels,
- a 2× close-up around the pointer for reading small text.

It answers in speech and ends with pointing tags (perception/pointing.py),
which are stripped from the answer and resolved to screen points for the
overlay. A pixel-only point is refined once on a native-resolution crop
around it (clicky-windows). The last HISTORY_TURNS exchanges of a talk
session are kept, without tags, so follow-ups work.
"""
from __future__ import annotations

import math
import threading
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .core.config import ROOT
from .core.llm import image_block
from .perception import accessibility as ax
from .perception import grounding, pointing, screen
from .perception.pointing import PointContext, Target

MAX_LIST = 60
HISTORY_TURNS = 10
MAX_SESSIONS = 20
REFINE_HALF_PT = 150.0
REFINE_MAX_SHIFT_PT = 160.0

_FALLBACK_PROMPT = ("Answer the user's question about their screen in one to three short "
                    "spoken sentences. End with [POINT:eN:label] tags for elements of the "
                    "numbered list, or [POINT:none].")
_REFINE_SYSTEM = ("You locate one thing in an image. Reply with only JSON: "
                  '{"x": <int>, "y": <int>} in this image\'s pixels, or {"x": null} if it is '
                  "not in the image.")


def talk_prompt() -> str:
    try:
        text = (ROOT / "shared" / "prompts" / "talk.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return _FALLBACK_PROMPT
    return text or _FALLBACK_PROMPT


# ---- sessions ----------------------------------------------------------------------------

@dataclass
class TalkSession:
    id: str
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY_TURNS))

    def messages(self) -> list[dict]:
        out: list[dict] = []
        for question, answer in self.history:
            out += [{"role": "user", "content": question},
                    {"role": "assistant", "content": answer or "(pointed)"}]
        return out


_SESSIONS: OrderedDict[str, TalkSession] = OrderedDict()
_sessions_lock = threading.Lock()


def get_session(session_id: str | None) -> TalkSession:
    with _sessions_lock:
        if session_id and session_id in _SESSIONS:
            _SESSIONS.move_to_end(session_id)
            return _SESSIONS[session_id]
        sess = TalkSession(session_id or uuid.uuid4().hex[:12])
        _SESSIONS[sess.id] = sess
        while len(_SESSIONS) > MAX_SESSIONS:
            _SESSIONS.popitem(last=False)
        return sess


# ---- what the model sees -----------------------------------------------------------------------

@dataclass
class Scene:
    point_ctx: PointContext
    content: list[dict]                 # Anthropic user content blocks
    full_capture: screen.Capture
    under_cursor: dict | None = None
    numbered: dict[int, Any] = field(default_factory=dict)


def number_elements(elements: list, cap: screen.Capture, cursor: tuple[float, float] | None,
                    limit: int = MAX_LIST) -> dict[int, Any]:
    """Up to ``limit`` labelled elements on the captured display, nearest the
    pointer first, numbered e1… in reading order."""
    d = cap.display
    area = d.width * d.height if d.known else 0.0
    usable = []
    for el in elements:
        label = (el.title or el.value or "").strip()
        if not label or el.w < 3 or el.h < 3:
            continue
        cx, cy = el.x + el.w / 2.0, el.y + el.h / 2.0
        if d.known and not d.contains(cx, cy):
            continue
        if area and el.w * el.h > 0.6 * area:
            continue
        dist = math.hypot(cx - cursor[0], cy - cursor[1]) if cursor else 0.0
        usable.append((dist, el))
    usable.sort(key=lambda t: t[0])
    chosen = sorted((el for _, el in usable[:limit]), key=lambda e: (round(e.y / 10), e.x))
    return {i + 1: el for i, el in enumerate(chosen)}


def describe_elements(numbered: dict[int, Any], cap: screen.Capture) -> str:
    lines = []
    for n, el in numbered.items():
        px, py = cap.to_pixels(el.x + el.w / 2.0, el.y + el.h / 2.0)
        label = (el.title or el.value or "").strip().replace("\n", " ")[:60]
        state = "" if el.enabled else " (disabled)"
        lines.append(f"e{n} {el.role.removeprefix('AX')}{state} '{label}' at "
                     f"({int(px)},{int(py)})")
    return "\n".join(lines)


def describe_under_cursor(info: dict | None, selected: str) -> str:
    if not info:
        return "Under the pointer: nothing readable (no accessibility info)."
    el = info.get("element") or {}
    parts = [f"Under the pointer: {str(el.get('role', '')).removeprefix('AX')} "
             f"'{(el.get('title') or '')[:80]}'"]
    if el.get("value"):
        parts.append(f"value '{str(el['value'])[:160]}'")
    if el.get("help"):
        parts.append(f"tooltip '{str(el['help'])[:120]}'")
    where = [f"app {info['app']}" if info.get("app") else "",
             f"window '{info['window'][:60]}'" if info.get("window") else "",
             "inside " + " › ".join(reversed(info.get("ancestry") or [])) if info.get("ancestry")
             else "", f"page {info['url'][:200]}" if info.get("url") else ""]
    text = ", ".join(parts) + ". " + "; ".join(w for w in where if w)
    if selected:
        text += f"\nSelected text: {selected[:800]}"
    return text


def build_scene(cursor: tuple[float, float] | None = None, *,
                capture: Callable[..., screen.Capture] | None = None,
                element_at: Callable[[float, float], dict | None] | None = None,
                read_tree: Callable[..., list] | None = None,
                selected_text: Callable[[], str] | None = None,
                close_up: bool = True, redact: Callable[[str], str] | None = None) -> Scene:
    """Capture and describe the screen around the pointer."""
    cursor = cursor or screen.cursor_position()
    display = screen.display_for_point(*cursor) if cursor else None
    full = (capture or screen.capture)(display)
    edge = int(screen.grounding_settings().get("max_image_edge") or 0) or None
    model_path = screen.resized_copy(full.path, edge) if edge else full.path
    model_cap = screen.capture_info(model_path) or full
    info = (element_at or ax.element_at)(*cursor) if cursor else None
    pid = None
    if info and info.get("app"):
        running = ax.resolve_app(info["app"])
        pid = int(running["pid"]) if running else None
    elements = (read_tree or ax.read_tree)(max_elements=300, pid=pid)
    numbered = number_elements(elements, full, cursor)
    try:
        sel = (selected_text or _selected_text_ax_only)()
    except Exception:  # noqa: BLE001
        sel = ""
    clean = redact or (lambda t: t)
    context = clean(describe_under_cursor(info, sel))
    listing = clean(describe_elements(numbered, model_cap))
    content: list[dict] = [
        {"type": "text", "text": context},
        {"type": "text", "text": "Numbered elements (coordinates are screenshot-1 pixels):\n"
                                 + (listing or "(none readable)")},
        {"type": "text", "text": "Screenshot 1: " + model_cap.label()},
        image_block(model_path),
    ]
    if close_up and cursor:
        try:
            crop = screen.crop_around(full, cursor[0], cursor[1], half_pt=110, scale=2.0)
            content += [{"type": "text", "text": "Close-up around the pointer, enlarged 2× "
                                                 "(for reading text only):"},
                        image_block(crop.path)]
        except Exception:  # noqa: BLE001 — the close-up is a nicety
            pass
    ctx = PointContext(captures=[model_cap], elements=numbered, snap_elements=list(elements),
                       coord_space=grounding.resolve_coord_space(
                           screen.grounding_settings().get("coord_space")))
    return Scene(ctx, content, full, info, numbered)


def _selected_text_ax_only() -> str:
    from .effectors import system

    return system.get_selected_text(copy_fallback=False)


# ---- asking ------------------------------------------------------------------------------------

@dataclass
class TalkReply:
    answer: str
    targets: list[Target]
    raw: str
    session_id: str
    talk_ms: float
    refined: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"answer": self.answer, "targets": [t.as_dict() for t in self.targets],
                "session_id": self.session_id, "talk_ms": round(self.talk_ms, 1),
                "refined": self.refined}


def _step(client: Any, system: str, messages: list[dict]) -> Any:
    """One model call, with its token cost recorded like an agent step."""
    resp = client.step(system, messages, [])
    try:
        from .core.metrics import MetricsCollector
        from .core.orchestrator import record_usage_for_response

        record_usage_for_response(MetricsCollector.get(), resp)
    except Exception:  # noqa: BLE001
        pass
    return resp


def _reply_text(resp: Any) -> str:
    text = (getattr(resp, "text", "") or "").strip()
    if text:
        return text
    for block in getattr(resp, "raw_content", None) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return str(block.get("text", "")).strip()
    return ""


def refine_target(target: Target, scene: Scene, client: Any) -> Target:
    """Ask again on a native-resolution crop around a pixel-only point."""
    crop = screen.crop_around(scene.full_capture, target.x, target.y,
                              half_pt=REFINE_HALF_PT, scale=1.0)
    what = target.label or "the thing the user asked about"
    content = [{"type": "text", "text": f"This is a close crop of the screen. Where is the "
                                        f"center of '{what}'? Beware of similar-looking "
                                        "neighbours."},
               image_block(crop.path)]
    try:
        resp = _step(client, _REFINE_SYSTEM, [{"role": "user", "content": content}])
    except Exception:  # noqa: BLE001 — keep the rough point
        return target
    pt = grounding.parse_point(_reply_text(resp))
    if pt is None:
        return target
    x, y = pt
    if scene.point_ctx.coord_space == "norm1000" and crop.width and crop.height:
        x, y = x / 1000.0 * crop.width, y / 1000.0 * crop.height
    gx, gy = crop.to_points(x, y)
    if math.hypot(gx - target.x, gy - target.y) > REFINE_MAX_SHIFT_PT:
        return target
    el = pointing.snap(gx, gy, scene.point_ctx.snap_elements, scene.full_capture)
    if el is not None:
        return Target("point", el.x + el.w / 2.0, el.y + el.h / 2.0, target.label,
                      float(el.w), float(el.h), source="snap", display=target.display)
    return Target("point", gx, gy, target.label, source="refined", display=target.display)


def ask(question: str, client: Any, *, cursor: tuple[float, float] | None = None,
        session_id: str | None = None, refine: bool = True, scene: Scene | None = None,
        redact: Callable[[str], str] | None = None) -> TalkReply:
    """Answer a spoken question about what is on screen, with pointing targets."""
    started = time.monotonic()
    scene = scene or build_scene(cursor, redact=redact)
    sess = get_session(session_id)
    messages = sess.messages() + [{"role": "user", "content": [
        *scene.content, {"type": "text", "text": f"The user asks: {question}"}]}]
    resp = _step(client, talk_prompt(), messages)
    raw = _reply_text(resp)
    tags = pointing.parse_tags(raw)
    answer = pointing.strip_tags(raw)
    targets = pointing.resolve_all(tags, scene.point_ctx)
    refined = 0
    if refine:
        out = []
        for t in targets:
            if t.kind == "point" and t.source == "image":
                new = refine_target(t, scene, client)
                refined += int(new is not t)
                out.append(new)
            else:
                out.append(t)
        targets = out
    sess.history.append((question, answer))
    return TalkReply(answer or "I'm not sure.", targets, raw, sess.id,
                     (time.monotonic() - started) * 1000, refined)
