"""Phase 10: pack write-back learning + proactive app-watcher triggers."""
from __future__ import annotations

import pytest

import aether.perception.app_watcher as aw
from aether.knowledge import learned, loader
from aether.perception.app_watcher import AppSnapshot, AppWatcher


# ---- pack learning ----

@pytest.fixture
def packs_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHER_PACKS_DIR", str(tmp_path))
    loader._load_pack_file.cache_clear()
    return tmp_path


def test_record_and_load_recipe(packs_dir):
    name = learned.record_success(
        "finder", "go to the Downloads folder",
        ["open_app Finder", "finder_go_to ~/Downloads"])
    assert name == "go_to_the_downloads"
    assert "go_to_the_downloads" in learned.load_learned("finder")["recipes"]


def test_single_step_not_learned(packs_dir):
    assert learned.record_success("finder", "x", ["one step only"]) is None


def test_duplicate_recipe_not_rewritten(packs_dir):
    steps = ["a", "b"]
    assert learned.record_success("mail", "send report", steps) is not None
    assert learned.record_success("mail", "send report", steps) is None  # identical


def test_recipe_cap(packs_dir):
    for i in range(learned.MAX_LEARNED_RECIPES + 5):
        learned.record_success("notes", f"task number {i} here", [f"step {i}", "done"])
    recipes = learned.load_learned("notes")["recipes"]
    assert len(recipes) <= learned.MAX_LEARNED_RECIPES


def test_prompt_slice_merges_learned(packs_dir):
    # a minimal bundled-style pack in the sideload dir + a learned recipe
    (packs_dir / "spotify.yaml").write_text("app: Spotify\ntier: 2\n")
    loader._load_pack_file.cache_clear()
    learned.record_success("spotify", "play my playlist",
                            ["open_app Spotify", "click Play"])
    out = loader.prompt_slice("Spotify")
    assert "Learned from your past successful runs" in out
    assert "play_my_playlist" in out


# ---- proactive triggers ----

@pytest.fixture
def watcher(monkeypatch):
    AppWatcher.reset()
    monkeypatch.setattr(aw.ax, "resolve_app",
                        lambda name: {"name": "Xcode", "pid": 7, "bundle": "x", "active": False})
    monkeypatch.setattr(aw, "_take_snapshot", lambda name: AppSnapshot(app="Xcode", pid=7))
    w = AppWatcher.get()
    w._stop.set()  # don't spin the poll thread
    yield w
    AppWatcher.reset()


def test_trigger_fires_on_matching_change(watcher):
    got: list[dict] = []
    watcher.set_event_sink(got.append)
    watcher.add_trigger("Xcode", contains="Build Succeeded", goal="run the tests")
    watcher._emit({"app": "Xcode", "kind": "window_change",
                   "detail": "windows now: ['Build Succeeded']"})
    triggers = [e for e in got if e.get("kind") == "app_trigger"]
    assert len(triggers) == 1
    assert triggers[0]["goal"] == "run the tests"


def test_trigger_does_not_fire_on_nonmatch(watcher):
    got: list[dict] = []
    watcher.set_event_sink(got.append)
    watcher.add_trigger("Xcode", contains="Build Succeeded", goal="run the tests")
    watcher._emit({"app": "Xcode", "kind": "content_change", "detail": "still compiling"})
    assert not any(e.get("kind") == "app_trigger" for e in got)


def test_trigger_no_recursion(watcher):
    # an app_trigger event must not itself re-trigger (infinite loop guard)
    got: list[dict] = []
    watcher.set_event_sink(got.append)
    watcher.add_trigger("Xcode", contains="", goal="do it")   # matches any change
    watcher._emit({"app": "Xcode", "kind": "content_change", "detail": "anything"})
    triggers = [e for e in got if e.get("kind") == "app_trigger"]
    assert len(triggers) == 1   # exactly one, not runaway


def test_trigger_cooldown_prevents_storm(watcher):
    # a continuously-changing app must not refire the trigger every poll
    got: list[dict] = []
    watcher.set_event_sink(got.append)
    watcher.add_trigger("Xcode", contains="", goal="do it")
    for i in range(5):  # 5 rapid changes within the cooldown window
        watcher._emit({"app": "Xcode", "kind": "content_change", "detail": f"change {i}"})
    triggers = [e for e in got if e.get("kind") == "app_trigger"]
    assert len(triggers) == 1   # fired once, then cooled down


def test_add_trigger_reports_watch_failure(monkeypatch):
    AppWatcher.reset()
    monkeypatch.setattr(aw.ax, "resolve_app", lambda name: None)  # app not running
    w = AppWatcher.get()
    w._stop.set()
    result = w.add_trigger("Ghost", contains="x", goal="do y")
    assert result.startswith("ERROR")   # not a false success
    AppWatcher.reset()


def test_type_text_description_hides_secret():
    from aether.tools.registry import DEFAULT_REGISTRY
    desc = DEFAULT_REGISTRY.describe_call("type_text", {"text": "hunter2-secret-pw"})
    assert "hunter2" not in desc
    assert "chars" in desc            # describes length, not content
