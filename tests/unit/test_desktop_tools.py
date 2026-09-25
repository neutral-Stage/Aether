"""Desktop tools: files, menus, windows/system helpers, scroll/drag/wait, paste."""
from __future__ import annotations

import pytest

from aether.effectors import clipboard, files, menus, system
from aether.tools import desktop_tools as dt
from aether.tools.registry import DEFAULT_REGISTRY, AgentContext


# ---- files -------------------------------------------------------------------

def test_write_read_list_roundtrip(tmp_path) -> None:  # noqa: ANN001
    f = tmp_path / "notes" / "a.txt"
    assert "Created" in files.write_file(str(f), "hello\nworld", mode="create")
    assert "hello" in files.read_file(str(f))
    assert "Appended" in files.write_file(str(f), "!", mode="append")
    assert f.read_text() == "hello\nworld!"
    with pytest.raises(FileExistsError):
        files.write_file(str(f), "x", mode="create")
    listing = files.list_dir(str(tmp_path / "notes"))
    assert "a.txt" in listing and "1 entries" in listing


def test_read_pages_and_binary(tmp_path) -> None:  # noqa: ANN001
    big = tmp_path / "big.txt"
    big.write_text("x" * 50)
    out = files.read_file(str(big), max_bytes=10)
    assert "pass offset=10" in out
    assert "offset=20" in files.read_file(str(big), max_bytes=10, offset=10)
    binf = tmp_path / "b.bin"
    binf.write_bytes(b"\x00\x01\x02" * 10)
    assert "binary file" in files.read_file(str(binf))


def test_list_dir_filters_hidden_and_pattern(tmp_path) -> None:  # noqa: ANN001
    (tmp_path / ".secret").write_text("s")
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    out = files.list_dir(str(tmp_path), pattern="*.md")
    assert "a.md" in out and "b.txt" not in out and ".secret" not in out


# ---- menus -------------------------------------------------------------------

@pytest.mark.parametrize(("wanted", "idx"), [
    ("Export as PDF", 1), ("export as pdf...", 1), ("Export", 0), ("pdf", 1), ("nope", None),
])
def test_match_title(wanted: str, idx) -> None:  # noqa: ANN001
    assert menus.match_title(["Export…", "Export as PDF…", "Print…"], wanted) == idx


def test_split_path_accepts_separators() -> None:
    assert menus.split_path("File > Export as PDF…") == ["File", "Export as PDF…"]
    assert menus.split_path("View → Sort By → Name") == ["View", "Sort By", "Name"]
    assert menus.split_path(["Edit", " Copy "]) == ["Edit", "Copy"]


def test_applescript_for_nested_menu() -> None:
    script = menus.applescript_for(["View", "Sort By", "Name"], "Finder")
    assert script == (
        'tell application "System Events" to tell process "Finder" to click menu item "Name" '
        'of menu "Sort By" of menu item "Sort By" of menu "View" of menu bar item "View" '
        'of menu bar 1')
    assert '\\"' in menus.applescript_for(['Say "hi"', "x"], "App")


def test_notification_script_escapes_quotes() -> None:
    s = system.notification_script('Done "now"', 'Back\\slash "quoted"')
    assert s == ('display notification "Back\\\\slash \\"quoted\\"" '
                 'with title "Done \\"now\\""')


# ---- handlers ----------------------------------------------------------------

@pytest.fixture
def ctx() -> AgentContext:
    return AgentContext(elements=[{"idx": 3, "x": 100, "y": 200, "w": 40, "h": 20}])


def test_scroll_targets_element_center(monkeypatch, ctx) -> None:  # noqa: ANN001
    calls = []
    monkeypatch.setattr(dt.kbd, "scroll", lambda **k: calls.append(k))
    monkeypatch.setattr(dt.time, "sleep", lambda s: None)
    out = dt._h_scroll({"direction": "down", "amount": 7, "element_index": 3}, ctx)  # noqa: SLF001
    assert calls == [{"dx": 0, "dy": -7, "x": 120.0, "y": 210.0}]
    assert "down 7" in out
    assert "ERROR" in dt._h_scroll({"direction": "sideways"}, ctx)  # noqa: SLF001


def test_drag_between_element_and_point(monkeypatch, ctx) -> None:  # noqa: ANN001
    calls = []
    monkeypatch.setattr(dt.kbd, "drag", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(dt.time, "sleep", lambda s: None)
    dt._h_drag({"from_index": 3, "to_x": 500, "to_y": 600}, ctx)  # noqa: SLF001
    assert calls[0][0] == (120.0, 210.0, 500.0, 600.0)
    assert "ERROR" in dt._h_drag({"from_index": 3}, ctx)  # noqa: SLF001


def test_wait_for_app_and_text(monkeypatch, ctx) -> None:  # noqa: ANN001
    monkeypatch.setattr(dt.time, "sleep", lambda s: None)
    monkeypatch.setattr(dt.ax, "frontmost_app", lambda: {"name": "Safari"})
    assert "is frontmost" in dt._h_wait({"for_app": "safari"}, ctx)  # noqa: SLF001
    seen = iter([False, False, True])
    monkeypatch.setattr(dt, "_screen_has_text", lambda t: next(seen))
    assert "on screen" in dt._h_wait({"for_text": "Done", "timeout": 5}, ctx)  # noqa: SLF001


def test_wait_times_out(monkeypatch, ctx) -> None:  # noqa: ANN001
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(dt.time, "monotonic", lambda: float(next(clock)))
    monkeypatch.setattr(dt.time, "sleep", lambda s: None)
    monkeypatch.setattr(dt, "_screen_has_text", lambda t: False)
    assert "Timed out" in dt._h_wait({"for_text": "never", "timeout": 10}, ctx)  # noqa: SLF001


def test_long_or_emoji_text_is_pasted(monkeypatch) -> None:
    pasted = []
    monkeypatch.setattr(clipboard, "paste_text", lambda t: pasted.append(t) or "Pasted")
    assert dt.needs_paste("x" * 201) and dt.needs_paste("hi 😀") and not dt.needs_paste("hi")
    DEFAULT_REGISTRY.dispatch("type_text", {"text": "😀 party"}, AgentContext())
    assert pasted == ["😀 party"]


def test_paste_restores_clipboard_only_if_unchanged(monkeypatch) -> None:
    restored = []
    monkeypatch.setattr(clipboard, "_OK", True)
    monkeypatch.setattr(clipboard, "snapshot", lambda: [{"t": b"old"}])
    monkeypatch.setattr(clipboard, "set_text", lambda t: 41)
    monkeypatch.setattr(clipboard, "restore", lambda items: restored.append(items))
    monkeypatch.setattr(clipboard.time, "sleep", lambda s: None)
    from aether.effectors import input as kbd
    monkeypatch.setattr(kbd, "press_key", lambda *a, **k: None)
    monkeypatch.setattr(clipboard, "change_count", lambda: 41)
    clipboard.paste_text("hello")
    assert restored == [[{"t": b"old"}]]
    monkeypatch.setattr(clipboard, "change_count", lambda: 42)  # user copied something
    clipboard.paste_text("hello")
    assert len(restored) == 1


def test_descriptions_never_echo_content() -> None:
    assert "secret" not in DEFAULT_REGISTRY.describe_call(
        "write_file", {"path": "/tmp/x", "content": "secret"})
    assert "secret" not in DEFAULT_REGISTRY.describe_call("clipboard_set", {"text": "secret"})
    assert DEFAULT_REGISTRY.describe_call("menu_item", {"path": "File > Save"}) == "menu File › Save"


def test_new_tools_are_registered_with_permissions() -> None:
    expected = {"scroll": "input", "drag": "input", "hover": "input", "wait": "none",
                "read_file": "files", "write_file": "files", "list_dir": "files",
                "open_path": "files", "open_url": "network", "clipboard_get": "screen",
                "clipboard_set": "input", "menu_item": "input", "list_menus": "screen",
                "focus_window": "input", "set_window_frame": "input",
                "get_selected_text": "screen", "notify": "none"}
    for name, perm in expected.items():
        spec = DEFAULT_REGISTRY.get(name)
        assert spec is not None, name
        assert spec.permission == perm, name
