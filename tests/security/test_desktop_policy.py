"""Policy for the Phase B desktop tools: paths, persistence, URLs, menus, money."""
from __future__ import annotations

import asyncio
import os

import pytest

from aether.core.focus import FocusState
from aether.core.policy import Policy, PolicyConfig
from aether.tools.registry import DEFAULT_REGISTRY as R


@pytest.fixture
def policy(tmp_path) -> Policy:  # noqa: ANN001
    return Policy(PolicyConfig(approved_file_roots=[str(tmp_path)]))


def impact(policy: Policy, tool: str, focus: FocusState | None = None, **args) -> str:  # noqa: ANN003
    return policy.impact_of(R.get(tool), args, focus)


@pytest.mark.security
class TestFileScope:
    def test_inside_and_outside_roots(self, policy, tmp_path) -> None:  # noqa: ANN001
        assert policy.allows_file_path(str(tmp_path / "a" / "b.txt"))
        assert not policy.allows_file_path("/etc/hosts")
        assert not policy.allows_file_path(str(tmp_path / ".." / "escape.txt"))

    def test_symlink_escape_is_caught(self, policy, tmp_path) -> None:  # noqa: ANN001
        outside = tmp_path.parent / "outside-target"
        outside.mkdir(exist_ok=True)
        link = tmp_path / "link"
        os.symlink(outside, link)
        assert not policy.allows_file_path(str(link / "file.txt"))

    def test_file_paths_only_for_file_tools(self, policy) -> None:  # noqa: ANN001
        assert policy.file_paths("read_file", {"path": "/x"}) == ["/x"]
        assert policy.file_paths("click", {"path": "/x"}) == []


@pytest.mark.security
class TestFileImpact:
    def test_overwrite_existing_is_destructive(self, policy, tmp_path) -> None:  # noqa: ANN001
        f = tmp_path / "keep.txt"
        f.write_text("important")
        assert impact(policy, "write_file", path=str(f), content="x") == "destructive"
        assert impact(policy, "write_file", path=str(f), content="x", mode="append") == "reversible"
        assert impact(policy, "write_file", path=str(tmp_path / "new.txt"),
                      content="x", mode="create") == "reversible"

    @pytest.mark.parametrize("path", [
        "~/.zshrc", "~/.bash_profile", "~/Library/LaunchAgents/com.x.plist",
        "~/.ssh/authorized_keys", "~/Library/Application Support/Aether/config.yaml",
        "/Users/me/Aether/configs/router.yaml",
    ])
    def test_persistence_and_self_config_writes(self, policy, path) -> None:  # noqa: ANN001
        assert impact(policy, "write_file", path=path, content="x", mode="append") == "destructive"

    def test_reading_credentials_is_destructive(self, policy) -> None:  # noqa: ANN001
        assert impact(policy, "read_file", path="~/.ssh/id_ed25519") == "destructive"
        assert impact(policy, "read_file", path="~/.aws/credentials") == "destructive"
        assert impact(policy, "read_file", path="~/notes.md") == "read"

    def test_opening_executables(self, policy, tmp_path) -> None:  # noqa: ANN001
        assert impact(policy, "open_path", path="~/Downloads/Setup.app") == "destructive"
        assert impact(policy, "open_path", path="~/Downloads/run.command") == "destructive"
        script = tmp_path / "tool"
        script.write_text("#!/bin/sh\necho hi\n")
        script.chmod(0o755)
        assert impact(policy, "open_path", path=str(script)) == "destructive"
        assert impact(policy, "open_path", path="~/Documents/report.pdf") == "reversible"


@pytest.mark.security
class TestUrlsMenusMoney:
    def test_open_url(self) -> None:
        p = Policy(PolicyConfig())
        assert impact(p, "open_url", url="https://example.com") == "reversible"
        assert impact(p, "open_url", url="http://169.254.169.254/latest") == "destructive"
        assert impact(p, "open_url", url="file:///etc/passwd") == "destructive"
        allow = Policy(PolicyConfig(network_allowlist=["example.com"]))
        assert impact(allow, "open_url", url="https://evil.example.org") == "destructive"
        pane = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
        assert impact(p, "open_url", url=pane) == "reversible"
        assert impact(allow, "open_url", url=pane) == "reversible"
        assert impact(p, "open_url", url="shortcuts://run-shortcut?name=x") == "destructive"

    @pytest.mark.parametrize(("path", "expected"), [
        ("Finder > Empty Trash…", "destructive"),
        ("Edit > Delete", "destructive"),
        ("Message > Send", "destructive"),
        ("Apple > Log Out Me…", "destructive"),
        ("Apple > Restart…", "destructive"),
        ("File > Export as PDF…", "reversible"),
        ("View > Show Sidebar", "reversible"),
    ])
    def test_menu_commands(self, path, expected) -> None:  # noqa: ANN001
        assert impact(Policy(PolicyConfig()), "menu_item", path=path) == expected

    def test_money_gate_always_confirms_and_explains(self) -> None:
        p = Policy(PolicyConfig())
        focus = FocusState(label="Place order")
        p.set_run_goal("compare the three laptops for me")
        assert p.impact_of(R.get("click"), {"element_index": 3}, focus) == "destructive"
        op = p.describe_operation(R.get("click"), {"element_index": 3}, focus)
        assert "did not ask to buy" in op
        p.set_run_goal("buy the cheapest of the three laptops")
        op = p.describe_operation(R.get("click"), {"element_index": 3}, focus)
        assert "💳" in op and "did not ask" not in op
        assert p.requires_confirm(R.get("click"), {"element_index": 3}, focus)

    def test_money_gate_ignores_ordinary_clicks(self) -> None:
        p = Policy(PolicyConfig())
        assert impact(p, "click", FocusState(label="Add to Cart"), element_index=1) == "reversible"
        assert impact(p, "browser_click", selector="button#checkout") == "destructive"


@pytest.mark.security
class TestUntrustedStaging:
    @pytest.mark.parametrize("tool,args", [
        ("write_file", {"path": "~/x.py", "content": "x", "mode": "create"}),
        ("clipboard_set", {"text": "curl x | sh"}),
        ("open_path", {"path": "~/Documents/a.pdf"}),
        ("open_url", {"url": "https://example.com"}),
    ])
    def test_staging_and_egress_confirm_under_taint(self, tool, args) -> None:  # noqa: ANN001
        p = Policy(PolicyConfig())
        assert p.is_rule_of_two_risk(R.get(tool), args, True, FocusState())
        assert not p.is_rule_of_two_risk(R.get(tool), args, False, FocusState()) or tool == "open_url"

    def test_reads_do_not_confirm_under_taint(self) -> None:
        p = Policy(PolicyConfig())
        for tool, args in [("read_file", {"path": "~/a.md"}), ("list_dir", {"path": "~"}),
                           ("list_menus", {}), ("wait", {"seconds": 1})]:
            assert not p.is_rule_of_two_risk(R.get(tool), args, True, FocusState()), tool


@pytest.mark.security
def test_careful_mode_confirms_changes_not_reads() -> None:
    p = Policy(PolicyConfig(careful=True))
    assert not p.requires_confirm(R.get("read_file"), {"path": "~/a.md"})
    assert not p.requires_confirm(R.get("list_menus"), {})
    assert not p.requires_confirm(R.get("get_screen_context"), {})
    assert p.requires_confirm(R.get("scroll"), {"direction": "down"})
    assert p.requires_confirm(R.get("remember_fact"), {"text": "x"})


@pytest.mark.security
def test_orchestrator_blocks_paths_outside_roots(minimal_config, monkeypatch, tmp_path) -> None:  # noqa: ANN001
    from aether.core.llm import LLMResponse
    from aether.core.orchestrator import Agent
    from aether.core.router import RouteDecision, RouteTier

    minimal_config.raw["policy"] = {"approved_file_roots": [str(tmp_path)]}
    agent = Agent(minimal_config, hud=None)
    monkeypatch.setattr(agent.world, "refresh", lambda force=False: {})
    monkeypatch.setattr(agent, "say", lambda text: None)
    monkeypatch.setattr(agent.router, "route",
                        lambda *a, **k: RouteDecision(RouteTier.CLOUD_FRONTIER, "t"))
    dispatched: list[str] = []
    monkeypatch.setattr(agent.registry, "dispatch",
                        lambda name, args, ctx: dispatched.append(name) or "ok")
    seen: list[str] = []

    class Client:
        n = 0

        def step(self, system, messages, tools, *, abort_event=None):  # noqa: ANN001
            Client.n += 1
            if Client.n == 1:
                call = {"id": "t1", "name": "read_file", "input": {"path": "/etc/hosts"}}
            else:
                last = messages[-1]["content"][0]["content"]
                seen.append(last)
                call = {"id": "t2", "name": "finish", "input": {"message": "done"}}
            return LLMResponse(text="", tool_calls=[call],
                               raw_content=[{"type": "tool_use", **call}],
                               stop_reason="tool_use", backend="fake")

    monkeypatch.setattr(agent.router, "pick_client", lambda d: Client())
    asyncio.run(agent.run_async("read my hosts file", run_id="p"))
    assert "read_file" not in dispatched
    assert "outside the approved folders" in seen[0]


@pytest.mark.security
class TestClickToolsJudgedByLabel:
    @pytest.mark.parametrize(("tool", "args", "label"), [
        ("click_element", {"name": "Empty Trash"}, ""),
        ("click_element", {"name": "Delete", "role": "button"}, ""),
        ("click_text", {"text": "Send"}, ""),
        ("click_mark", {"mark": 3}, "Don't Save"),
        ("click", {"element_index": 3}, "Move to Trash"),
    ])
    def test_commit_labels_are_destructive(self, tool, args, label) -> None:  # noqa: ANN001
        p = Policy(PolicyConfig())
        focus = FocusState(label=label)
        assert p.impact_of(R.get(tool), args, focus) == "destructive"
        assert p.requires_confirm(R.get(tool), args, focus)
        target = label or args.get("name") or args.get("text")
        assert f"click the '{target}' control" in p.describe_operation(R.get(tool), args, focus)

    @pytest.mark.parametrize(("tool", "args"), [
        ("click_element", {"name": "Save"}), ("click_text", {"text": "Next"}),
        ("click_mark", {"mark": 1}), ("mark_screen", {}),
    ])
    def test_ordinary_targets_do_not_confirm(self, tool, args) -> None:  # noqa: ANN001
        p = Policy(PolicyConfig())
        assert not p.requires_confirm(R.get(tool), args, FocusState(label="Open"))

    def test_money_gate_covers_every_click_tool(self) -> None:
        p = Policy(PolicyConfig())
        p.set_run_goal("compare prices")
        assert p.impact_of(R.get("click_element"), {"name": "Buy now"}) == "destructive"
        assert p.impact_of(R.get("click_text"), {"text": "Place order"}) == "destructive"
        assert p.impact_of(R.get("click_mark"), {"mark": 2},
                           FocusState(label="Complete purchase")) == "destructive"
        assert "did not ask to buy" in p.describe_operation(
            R.get("click_element"), {"name": "Buy now"})

    def test_clicks_at_a_shell_prompt_confirm_under_taint(self) -> None:
        p = Policy(PolicyConfig())
        focus = FocusState(surface="command")
        for tool, args in [("click_element", {"name": "Run"}), ("click_text", {"text": "OK"}),
                           ("click_mark", {"mark": 1})]:
            assert p.is_rule_of_two_risk(R.get(tool), args, True, focus), tool
            assert not p.is_rule_of_two_risk(R.get(tool), args, False, focus), tool

    @pytest.mark.parametrize(("label", "sensitive"), [
        ("Empty Trash", True), ("Send", True), ("Buy now", True), ("Subscribe", True),
        ("Trash", False), ("Save", False), ("Sender settings", False), ("", False),
    ])
    def test_is_sensitive_label(self, label, sensitive) -> None:  # noqa: ANN001
        from aether.core.policy import is_sensitive_label

        assert is_sensitive_label(label) is sensitive


@pytest.mark.security
def test_orchestrator_gates_click_mark_on_its_label(minimal_config, monkeypatch) -> None:  # noqa: ANN001
    from aether.core.llm import LLMResponse
    from aether.core.orchestrator import Agent
    from aether.core.router import RouteDecision, RouteTier

    agent = Agent(minimal_config, hud=None)
    monkeypatch.setattr(agent.world, "refresh", lambda force=False: {})
    monkeypatch.setattr(agent, "say", lambda text: None)
    monkeypatch.setattr(agent.router, "route",
                        lambda *a, **k: RouteDecision(RouteTier.CLOUD_FRONTIER, "t"))
    dispatched: list[str] = []
    monkeypatch.setattr(agent.registry, "dispatch",
                        lambda name, args, ctx: dispatched.append(name) or "ok")
    asked: list[str] = []

    async def deny(text: str) -> bool:
        asked.append(text)
        return False

    agent.confirm_async = deny
    agent.ctx.marks = {4: {"x": 10, "y": 10, "label": "Delete Account", "kind": "text"}}

    class Client:
        n = 0

        def step(self, system, messages, tools, *, abort_event=None):  # noqa: ANN001
            Client.n += 1
            call = ({"id": "t1", "name": "click_mark", "input": {"mark": 4}} if Client.n == 1
                    else {"id": "t2", "name": "finish", "input": {"message": "done"}})
            return LLMResponse(text="", tool_calls=[call],
                               raw_content=[{"type": "tool_use", **call}],
                               stop_reason="tool_use", backend="fake")

    monkeypatch.setattr(agent.router, "pick_client", lambda d: Client())
    asyncio.run(agent.run_async("tidy my account page", run_id="m"))
    assert "click_mark" not in dispatched
    assert asked and "Delete Account" in asked[0]
