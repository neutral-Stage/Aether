"""Background-app click/type routing + get_app_context tool (mocked AX)."""
from __future__ import annotations

import aether.tools.registry as reg
from aether.tools.registry import AgentContext, _h_click, _h_type_text, _h_get_app_context


def test_background_click_uses_app_handle(monkeypatch):
    pressed = {}
    monkeypatch.setattr(reg.ax_actions, "app_handle",
                        lambda app, idx: f"handle-{app}-{idx}")
    def _press(h, label=""):
        pressed["h"] = h
        return f"pressed {label}"
    monkeypatch.setattr(reg.ax_actions, "press_handle", _press)
    out = _h_click({"app": "Mail", "element_index": 3}, AgentContext())
    assert pressed["h"] == "handle-Mail-3"
    assert "pressed" in out


def test_background_click_missing_handle(monkeypatch):
    monkeypatch.setattr(reg.ax_actions, "app_handle", lambda app, idx: None)
    out = _h_click({"app": "Mail", "element_index": 9}, AgentContext())
    assert "get_app_context" in out and "ERROR" in out


def test_background_type_requires_element_index():
    out = _h_type_text({"app": "Notes", "text": "hi"}, AgentContext())
    assert "element_index" in out and "ERROR" in out


def test_background_type_sets_value(monkeypatch):
    monkeypatch.setattr(reg.ax_actions, "app_handle", lambda app, idx: "h1")
    monkeypatch.setattr(reg.ax_actions, "set_value_handle",
                        lambda h, text, label="": f"set {len(text)} on {label}")
    out = _h_type_text({"app": "Notes", "text": "hello", "element_index": 2}, AgentContext())
    assert "set 5" in out


def test_foreground_click_still_works(monkeypatch):
    monkeypatch.setattr(reg, "_try_native_effector", lambda tool, args: None)
    monkeypatch.setattr(reg.ax_actions, "can_press", lambda idx: False)
    clicked = {}
    monkeypatch.setattr(reg.kbd, "click",
                        lambda x, y, button="left", count=1: clicked.update(x=x, y=y))
    ctx = AgentContext(elements=[{"idx": 0, "x": 10, "y": 20, "w": 4, "h": 4}])
    out = _h_click({"element_index": 0}, ctx)
    assert clicked == {"x": 12.0, "y": 22.0}
    assert "Clicked" in out


def test_get_app_context_handler(monkeypatch):
    monkeypatch.setattr(reg.ax, "app_context",
                        lambda app: {"app": "Mail", "active": False, "element_count": 2,
                                     "rendered": "[0] Button"})
    out = _h_get_app_context({"app": "Mail"}, AgentContext())
    assert "background" in out and "Mail" in out


def test_get_app_context_not_running(monkeypatch):
    monkeypatch.setattr(reg.ax, "app_context",
                        lambda app: {"error": "app not running: Ghost", "element_count": 0,
                                     "elements": [], "rendered": ""})
    out = _h_get_app_context({"app": "Ghost"}, AgentContext())
    assert "ERROR" in out and "not running" in out
