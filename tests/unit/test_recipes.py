"""Pack schema v2: verified recipes, verification stamps, Shortcuts."""
from __future__ import annotations

import json
import subprocess

import pytest

from aether.core.focus import FocusState
from aether.core.policy import Policy, PolicyConfig
from aether.effectors import system
from aether.guide import plan
from aether.knowledge import loader
from aether.knowledge.validator import validate_pack_data
from aether.tools.registry import DEFAULT_REGISTRY as R
from tests.benchmark import vm_runner


def _all_recipes() -> list[tuple[str, str, dict]]:
    out = []
    for key in loader.list_packs():
        pack = loader._load_pack_file(key) or {}  # noqa: SLF001
        for name, recipe in (pack.get("verified_recipes") or {}).items():
            out.append((key, name, recipe))
    return out


RECIPES = _all_recipes()


def test_packs_ship_recipes() -> None:
    assert len(RECIPES) >= 10
    assert json.loads(loader.VERIFIED_PATH.read_text()) is not None


@pytest.mark.parametrize(("key", "name", "recipe"), RECIPES, ids=[f"{k}.{n}" for k, n, _ in RECIPES])
def test_recipe_steps_are_real_tools_needing_no_confirmation(key, name, recipe) -> None:  # noqa: ANN001
    policy = Policy(PolicyConfig())
    for step in recipe["steps"]:
        spec = R.get(step["tool"])
        assert spec is not None, step["tool"]
        missing = [k for k in spec.json_schema.get("required") or [] if k not in step["args"]]
        assert not missing, (step, missing)
        assert not policy.requires_confirm(spec, step["args"], FocusState()), step
    if recipe.get("guide"):
        assert len(plan.parse_steps_data(recipe["guide"])) == len(recipe["guide"])


def test_matching_prefers_the_longest_phrase_and_is_strict_across_packs() -> None:
    pack = {"verified_recipes": {
        "short": {"match": ["note"], "steps": [{"tool": "wait", "args": {}}]},
        "long": {"match": ["make a note"], "steps": [{"tool": "wait", "args": {}}]}}}
    assert loader.match_verified_recipe(pack, "please make a note")[0] == "long"
    assert loader.match_verified_recipe(pack, "a note")[0] == "short"
    assert loader.match_verified_recipe(pack, "a note", min_words=2) is None
    assert loader.match_verified_recipe(pack, "") is None
    assert loader.verified_recipe_for("I got a new note from Bob") is None
    assert loader.verified_recipe_for("take a note: call mom")[:2] == ("notes", "create_note")


def test_validator_rejects_bad_recipes() -> None:
    bad = {"app": "X", "tier": 1, "verified_recipes": {
        "a": {"match": [], "steps": []},
        "b": {"match": ["x y"], "steps": [{"args": {}}], "guide": [{"say": ""}]},
        "c": {"match": ["x y"], "steps": [{"tool": "wait"}], "guide": [{"say": "s", "done_when": "fly"}]}}}
    errors = validate_pack_data(bad)
    assert any("'match' must be" in e for e in errors)
    assert any("step 1 needs a tool" in e for e in errors)
    assert any("needs 'say'" in e for e in errors)
    assert any("unknown done_when" in e for e in errors)


def test_render_and_stamp(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(loader, "VERIFIED_PATH", tmp_path / "verified.json")
    recipe = {"steps": [{"tool": "open_app", "args": {"name": "Notes"}}]}
    assert "not yet tested" in loader.render_verified_recipe("notes", "create_note", recipe)
    assert loader.stamp_verified(["notes.create_note"], "2026-09-25") == 1
    text = loader.render_verified_recipe("notes", "create_note", recipe)
    assert "tested on a Mac on 2026-09-25" in text and '1. open_app {"name": "Notes"}' in text


def test_prompt_slice_and_agent_prompt_carry_recipes(minimal_config) -> None:  # noqa: ANN001
    assert "Recipe for this task (create_note" in loader.prompt_slice("Notes", "make a note")
    from aether.core.orchestrator import Agent

    minimal_config.raw["knowledge"] = {"enabled": True}
    agent = Agent(minimal_config, hud=None)
    agent.world.frontmost_app = "Finder"
    prompt = agent._system_prompt("take a note that says call mom")  # noqa: SLF001
    assert "Recipe for this task (create_note" in prompt


def test_benchmark_tasks_link_to_real_recipes() -> None:
    names = {f"{k}.{n}" for k, n, _ in RECIPES}
    linked = [t for t in vm_runner.load_live_tasks() if t.get("recipe")]
    assert len(linked) >= 6
    assert {t["recipe"] for t in linked} <= names
    results = [vm_runner.LiveResult(t["id"], i % 2 == 0, "") for i, t in enumerate(linked)]
    passed = vm_runner.passed_recipes(linked, results)
    assert passed == sorted({t["recipe"] for i, t in enumerate(linked) if i % 2 == 0})


def test_guide_uses_recipe_steps() -> None:
    steps = plan.recipe_steps("add a printer")
    assert [s.done_when for s in steps][:2] == ["menu", "app"]
    assert plan.recipe_steps("fold a paper crane") == []


# ---- Shortcuts -----------------------------------------------------------------------------

def test_shortcut_policy() -> None:
    spec = R.get("shortcuts_run")
    p = Policy(PolicyConfig())
    assert p.impact_of(spec, {"name": "Morning Routine"}) == "destructive"
    trusted = Policy(PolicyConfig(trusted_shortcuts=["morning routine"]))
    assert trusted.impact_of(spec, {"name": "Morning Routine"}) == "reversible"
    assert not trusted.requires_confirm(spec, {"name": "Morning Routine"})
    # untrusted content in context: even a trusted shortcut asks
    assert trusted.is_rule_of_two_risk(spec, {"name": "Morning Routine"}, True, FocusState())
    assert not p.requires_confirm(R.get("shortcuts_list"), {})


def test_shortcut_handlers(monkeypatch) -> None:
    calls = []

    def fake_run(argv, **kw):  # noqa: ANN001, ANN003, ANN202
        calls.append(argv)
        if argv[1] == "list":
            return subprocess.CompletedProcess(argv, 0, "Morning Routine\nResize Image\n", "")
        out = argv[argv.index("--output-path") + 1]
        with open(out, "w") as fh:
            fh.write("done!")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(system.subprocess, "run", fake_run)
    from aether.tools.registry import AgentContext

    assert "2 Shortcuts" in R.dispatch("shortcuts_list", {}, AgentContext())
    out = R.dispatch("shortcuts_run", {"name": "Resize Image", "input": "photo.png"}, AgentContext())
    assert "Output:\ndone!" in out
    assert calls[-1][:3] == ["shortcuts", "run", "Resize Image"] and "--input-path" in calls[-1]
