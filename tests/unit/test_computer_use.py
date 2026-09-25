"""Computer-use effector: request shape (real SDK, mocked transport) and action mapping."""
from __future__ import annotations

import json

import pytest

anthropic = pytest.importorskip("anthropic")
httpx2 = pytest.importorskip("httpx2")

from aether.effectors import computer_use as cu  # noqa: E402
from aether.perception.screen import Capture, DisplayInfo  # noqa: E402

DISPLAY = DisplayInfo(1, 1, 0.0, 0.0, 1512.0, 982.0, 2.0, True)


@pytest.fixture
def cap(tmp_path):  # noqa: ANN001, ANN201
    p = tmp_path / "s.png"
    p.write_bytes(b"\x89PNG fake")
    return Capture(str(p), DISPLAY, 1366, 887)


def test_request_uses_current_tool_and_beta(monkeypatch, cap) -> None:  # noqa: ANN001
    sent: list[tuple[dict, dict]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append((json.loads(request.content), dict(request.headers)))
        return httpx2.Response(200, json={
            "id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-5",
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 5, "output_tokens": 3},
            "content": [{"type": "tool_use", "id": "t", "name": "computer",
                         "input": {"action": "mouse_move", "coordinate": [683, 443]}}],
        })

    real = anthropic.Anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: real(
        api_key=kw["api_key"], http_client=httpx2.Client(transport=httpx2.MockTransport(handler))))
    point = cu.locate("Send button", api_key="k", cap=cap)
    body, headers = sent[0]
    assert body["model"] == "claude-sonnet-5"
    assert body["tools"][0] == {"type": "computer_20251124", "name": "computer",
                                "display_width_px": 1366, "display_height_px": 887}
    assert "computer-use-2025-11-24" in headers.get("anthropic-beta", "")
    assert "temperature" not in body
    # Image pixels → screen points: 683/1366*1512 = 756, 443/887*982 ≈ 490.4
    assert point == pytest.approx((756.0, 490.4), abs=0.5)


@pytest.fixture
def scripted(monkeypatch, cap):  # noqa: ANN001, ANN201
    calls: list[tuple] = []
    monkeypatch.setattr(cu, "_screenshot", lambda: cap)
    from aether.effectors import input as kbd
    monkeypatch.setattr(kbd, "click", lambda x, y, button="left", count=1: calls.append(("click", x, y, button, count)))
    monkeypatch.setattr(kbd, "move", lambda x, y: calls.append(("move", x, y)))
    monkeypatch.setattr(kbd, "drag", lambda *a, **k: calls.append(("drag", *a)))
    monkeypatch.setattr(kbd, "scroll", lambda **k: calls.append(("scroll", k)))
    monkeypatch.setattr(kbd, "type_text", lambda t: calls.append(("type", t)))
    monkeypatch.setattr(kbd, "press_key", lambda k, modifiers=None: calls.append(("key", k, modifiers)))

    def run(inp: dict) -> str:
        monkeypatch.setattr(cu, "_call", lambda *a, **k: (inp, "because"))
        return cu.computer_use_step("do it", api_key="k")
    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_double_click_maps_to_points(scripted) -> None:  # noqa: ANN001
    out = scripted({"action": "double_click", "coordinate": [1366, 887]})
    assert scripted.calls[-1][:3] == ("click", pytest.approx(1512), pytest.approx(982))
    assert scripted.calls[-1][4] == 2
    assert "double_click" in out


def test_scroll_and_drag(scripted) -> None:  # noqa: ANN001
    scripted({"action": "scroll", "coordinate": [683, 443], "scroll_direction": "down",
              "scroll_amount": 5})
    assert scripted.calls[-1][1]["dy"] == -5
    scripted({"action": "left_click_drag", "start_coordinate": [0, 0], "coordinate": [1366, 887]})
    assert scripted.calls[-1][0] == "drag"
    assert scripted.calls[-1][3:5] == (pytest.approx(1512), pytest.approx(982))


def test_key_combo(scripted) -> None:  # noqa: ANN001
    scripted({"action": "key", "text": "cmd+shift+t"})
    assert scripted.calls[-1] == ("key", "t", ["cmd", "shift"])


def test_no_action(scripted, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(cu, "_call", lambda *a, **k: (None, "nothing to do"))
    assert "proposed no action" in cu.computer_use_step("x", api_key="k")
