"""Secrets hidden from tool results (and counted), and the cost estimate."""
from __future__ import annotations

import asyncio

from aether.core import cost_history
from aether.core.metrics import MetricsCollector, RunMetrics
from aether.core.security import redact_tokens

FAKE_OPENAI = "sk-proj-" + "Ab3dE5gH7jK9mN1pQ3sT5vX7zB9dF1hJ"
FAKE_GITHUB = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
FAKE_AWS = "AKIA" + "IOSFODNN7EXAMPLE"
RANDOM_LOOKING = "Zq8vN2kLw5Rt9XyB3mHc7PdJ4sGf6TaE"


def test_known_formats_are_hidden_and_counted() -> None:
    text = (f"OPENAI_API_KEY={FAKE_OPENAI}\ntoken {FAKE_GITHUB}\n"
            f"aws {FAKE_AWS}\nAuthorization: Bearer abcdefghijklmnopqrstuvwxyz0123\n"
            "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIVATE KEY-----")
    out, n = redact_tokens(text)
    assert n == 5
    for secret in (FAKE_OPENAI, FAKE_GITHUB, FAKE_AWS, "abcdefghijklmnopqrstuvwxyz0123",
                   "b3BlbnNzaC1rZXk"):
        assert secret not in out
    assert "OPENAI_API_KEY=[REDACTED]" in out and "Bearer [REDACTED]" in out


def test_ordinary_text_and_code_are_left_alone() -> None:
    code = ('token = request.headers["X-Token"]\npassword_field = form.get("password")\n'
            "commit 3f9a1c2b7e4d5f6a8b9c0d1e2f3a4b5c6d7e8f90\n"
            "https://example.com/path/to/SomeVeryLongPageNameThatIsNotASecret123")
    assert redact_tokens(code, entropy=True) == (code, 0)
    # random-looking strings only with the screen check
    assert redact_tokens(f"code {RANDOM_LOOKING}") == (f"code {RANDOM_LOOKING}", 0)
    out, n = redact_tokens(f"code {RANDOM_LOOKING}", entropy=True)
    assert n == 1 and RANDOM_LOOKING not in out


def test_agent_hides_secrets_in_tool_results(minimal_config, monkeypatch) -> None:  # noqa: ANN001
    from aether.core.orchestrator import Agent

    agent = Agent(minimal_config, hud=None)
    events: list[dict] = []
    agent.emit = events.append
    monkeypatch.setattr(agent.registry, "dispatch",
                        lambda name, args, ctx: f"Window text: key {FAKE_OPENAI} and {RANDOM_LOOKING}")
    out = asyncio.run(agent._execute_call("get_screen_context", {}, step=2, rid="r"))  # noqa: SLF001
    assert FAKE_OPENAI not in out.content and RANDOM_LOOKING not in out.content
    red = [e for e in events if e["type"] == "redaction"]
    assert red == [{"type": "redaction", "step": 2, "tool": "get_screen_context", "count": 2,
                    "total": 2}]
    # file contents only lose recognised formats, so code read back stays intact
    monkeypatch.setattr(agent.registry, "dispatch",
                        lambda name, args, ctx: f"x = '{RANDOM_LOOKING}'")
    out = asyncio.run(agent._execute_call("read_file", {"path": "~/notes.txt"}, step=3,  # noqa: SLF001
                                          rid="r"))
    assert RANDOM_LOOKING in out.content and agent.redacted_count == 2


def test_redaction_can_be_turned_off(minimal_config, monkeypatch) -> None:  # noqa: ANN001
    from aether.core.orchestrator import Agent

    minimal_config.raw["policy"] = {"redact_secrets": False}
    agent = Agent(minimal_config, hud=None)
    monkeypatch.setattr(agent.registry, "dispatch", lambda name, args, ctx: FAKE_OPENAI)
    out = asyncio.run(agent._execute_call("get_screen_context", {}, step=1, rid="r"))  # noqa: SLF001
    assert out.content == FAKE_OPENAI


def _run(cost: float, steps: int = 4) -> RunMetrics:
    return RunMetrics(run_id="x", goal="g", status="idle", steps=steps, cost_usd=cost,
                      finished_at=1.0)


def test_cost_history_and_estimate(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(cost_history, "PATH", tmp_path / "costs.jsonl")
    assert cost_history.summary(cost_history.estimate(2.0)) == "No cost history yet · stops at $2.00"
    for c in (0.01, 0.02, 0.03, 0.05, 0.40):
        cost_history.record(_run(c))
    cost_history.record(_run(9.0, steps=0))           # never started: not kept
    est = cost_history.estimate(2.0)
    assert est["runs"] == 5 and est["typical_usd"] == 0.03 and est["high_usd"] == 0.40
    assert cost_history.summary(est) == "Tasks usually cost about $0.03 · stops at $2.00"
    assert cost_history.summary({"typical_usd": 0.001, "cap_usd": 0}) == (
        "Tasks usually cost under $0.01 · no cost limit")
    # the metrics hook feeds it
    m = MetricsCollector()
    m.on_run_end = cost_history.record
    m.start_run("r9", "goal")
    m.record_step("fast", 10.0)
    m.end_run("idle")
    assert cost_history.estimate(2.0)["runs"] == 6


def test_estimate_endpoint(sidecar_client, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(cost_history, "PATH", tmp_path / "c.jsonl")
    cost_history.record(_run(0.02))
    body = sidecar_client.get("/estimate").json()
    assert body["typical_usd"] == 0.02 and body["summary"].startswith("Tasks usually cost")
