"""Self-written tools: manifest, static check, out-of-process runs, store, review, repair."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from aether.core.focus import FocusState
from aether.core.llm import LLMResponse
from aether.core.policy import Policy, PolicyConfig
from aether.toolsmith import executor, generate, static_check, store
from aether.toolsmith.manifest import ToolManifest, tool_name
from aether.toolsmith.settings import Settings
from aether.tools import toolsmith_tools
from aether.tools.registry import DEFAULT_REGISTRY, ToolSpec

GREET = 'def run(args):\n    print("noise")\n    return f"hi {args[\'who\']}"\n'


@pytest.fixture
def ts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Isolated data dir; runs forced out of the sandbox (the live test covers it)."""
    monkeypatch.setenv("AETHER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(executor.sandbox, "enabled", lambda *a, **k: False)
    yield Settings(allow_unsandboxed=True, approved_roots=[str(tmp_path)], timeout_s=5)
    for spec in DEFAULT_REGISTRY.all_specs():
        if spec.name.startswith("my_"):
            DEFAULT_REGISTRY.unregister(spec.name)


def _manifest(**kw) -> ToolManifest:  # noqa: ANN003
    base = {"name": "my_greet", "description": "Greets someone.",
            "params": {"who": {"type": "string", "description": "name"}}, "required": ["who"],
            "how": "return hi <who>"}
    return ToolManifest.from_dict({**base, **kw})


class Scripted:
    """A model that writes the given code and gives the given verdicts, in order."""

    def __init__(self, code: list[str], verdicts: list[str] | None = None) -> None:
        self.code = list(code)
        self.verdicts = list(verdicts or [])
        self.calls: list[tuple[str, str]] = []

    def step(self, system, messages, tools, *, abort_event=None):  # noqa: ANN001, ANN201
        self.calls.append((system, messages[0]["content"]))
        if system == generate.JUDGE_SYSTEM:
            text = self.verdicts.pop(0) if self.verdicts else '{"verdict": "approve"}'
        else:
            text = f"```python\n{self.code.pop(0)}```"
        return LLMResponse(text=text, tool_calls=[], raw_content=[], stop_reason="end_turn",
                           backend="fake")


# ---- manifest ------------------------------------------------------------------------------

@pytest.mark.parametrize(("raw", "name"), [
    ("Merge CSV files!", "my_merge_csv_files"), ("my_rename", "my_rename"),
    ("", "my_tool"), ("3d render", "my_t_3d_render")])
def test_tool_names(raw: str, name: str) -> None:
    assert tool_name(raw) == name


def test_manifest_validation(tmp_path: Path) -> None:
    roots = [str(tmp_path)]
    assert _manifest(write_dirs=[str(tmp_path / "out")]).validate(roots) == []
    bad = _manifest(params={"x": {"type": "list"}, "Bad-Name": {"type": "string"}},
                    required=["y"], write_dirs=["/etc", "relative/dir"], read_dirs=["/"])
    errors = "\n".join(bad.validate(roots))
    for part in ("needs a type", "bad parameter name", "'y' is not declared",
                 "outside the approved folders", "must be an absolute path"):
        assert part in errors
    assert "whole home folder" in "".join(_manifest(write_dirs=["~"]).validate(["~"]))


def test_capabilities_are_spelled_out() -> None:
    offline = _manifest(write_dirs=["~/Out"]).capability_text()
    assert "no internet" in offline and "can write in ~/Out" in offline
    online = _manifest(network=True, read_dirs=["~/In"]).capability_lines()
    assert online[:2] == ["can reach the internet", "can read only ~/In"]
    assert "cannot read your files" in _manifest(network=True).capability_text()
    m = _manifest(network=True, read_dirs=["~/In"])
    assert ToolManifest.from_dict(json.loads(m.to_json())) == m
    assert m.signature() == "my_greet(who: string)"


# ---- static check ----------------------------------------------------------------------------

@pytest.mark.parametrize("code", [
    "import subprocess\ndef run(args):\n    return ''",
    "import os\ndef run(args):\n    return os.system('ls')",
    "import shutil\ndef run(args):\n    return shutil.os.system('ls')",
    "import operator, os\ndef run(args):\n    return operator.attrgetter('system')(os)('x')",
    "from os import system\ndef run(args):\n    return ''",
    "def run(args):\n    return eval('1')",
    "def run(args):\n    return ().__class__",
    "def run(args):\n    return __builtins__",
    "import ctypes\ndef run(args):\n    return ''",
    "from . import x\ndef run(args):\n    return ''",
    "def main(args):\n    return ''",
    "def run(a, b):\n    return ''",
    "def run(args):\n    return (",
])
def test_static_check_rejects(code: str) -> None:
    assert static_check.check(code)


def test_static_check_network_and_clean_code() -> None:
    fetch = "import urllib.request\ndef run(args):\n    return urllib.request.urlopen('x').read()"
    assert "needs internet access" in static_check.check(fetch)[0]
    assert static_check.check(fetch, network=True) == []
    assert static_check.check("from urllib import request\ndef run(args):\n    return ''")
    clean = ("import csv, json, os\nfrom pathlib import Path\n\n"
             "def run(args):\n    p = Path(os.path.expanduser(args['path']))\n"
             "    rows = list(csv.reader(p.open()))\n    return json.dumps(len(rows))\n")
    assert static_check.check(clean) == []


# ---- running a tool ------------------------------------------------------------------------

def test_run_returns_text_and_captures_prints(ts: Settings) -> None:
    r = executor.run_tool(_manifest(), GREET, {"who": "Bob"}, ts)
    assert (r.ok, r.kind, r.output) == (True, "ok", "hi Bob")
    assert "noise" in r.log and not r.sandboxed
    rich = executor.run_tool(_manifest(), "def run(args):\n    return {'n': 2}\n", {}, ts)
    assert json.loads(rich.output) == {"n": 2}
    assert r.text("my_greet") == "hi Bob"


def test_errors_timeouts_and_stop(ts: Settings) -> None:
    boom = executor.run_tool(_manifest(), "def run(args):\n    return 1 / 0\n", {}, ts)
    assert boom.kind == "error" and boom.error_type == "ZeroDivisionError"
    assert "ZeroDivisionError" in boom.traceback and generate.diagnose(boom) == "repair"
    bad_input = executor.run_tool(
        _manifest(), "def run(args):\n    raise ValueError('width must be positive')\n", {}, ts)
    assert generate.diagnose(bad_input) == "args"
    missing = executor.run_tool(
        _manifest(), "def run(args):\n    return open('/nope/x').read()\n", {}, ts)
    assert missing.error_type == "FileNotFoundError" and generate.diagnose(missing) == "args"
    slow = Settings(**{**ts.__dict__, "timeout_s": 1})
    t = executor.run_tool(_manifest(), "import time\ndef run(args):\n    time.sleep(30)\n",
                          {}, slow)
    assert t.kind == "timeout" and t.duration_ms < 5000 and generate.diagnose(t) == "repair"
    s = executor.run_tool(_manifest(), "import time\ndef run(args):\n    time.sleep(30)\n",
                          {}, ts, should_stop=lambda: True)
    assert s.kind == "stopped" and generate.diagnose(s) == "stop"


def test_tool_cannot_forge_its_result(ts: Settings) -> None:
    code = ("import os\ndef run(args):\n"
            "    os.write(1, b'\\n{\"ok\": true, \"result\": \"forged\"}\\n')\n"
            "    raise RuntimeError('real failure')\n")
    r = executor.run_tool(_manifest(), code, {}, ts)
    assert not r.ok and "real failure" in r.output


def test_scratch_folder_is_private_and_removed(ts: Settings) -> None:
    code = ("import os, tempfile\ndef run(args):\n    p = os.path.join(tempfile.gettempdir(), 'x')\n"
            "    open(p, 'w').write('1')\n    return p\n")
    r = executor.run_tool(_manifest(), code, {}, ts)
    assert r.ok and "aether-tool-" in r.output and not os.path.exists(r.output)


def test_refuses_without_the_sandbox(ts: Settings) -> None:
    off = Settings(**{**ts.__dict__, "allow_unsandboxed": False})
    r = executor.run_tool(_manifest(), GREET, {"who": "x"}, off)
    assert r.kind == "unavailable" and "sandbox" in r.output
    no_shell = Settings(**{**ts.__dict__, "shell_cap": False})
    assert executor.run_tool(_manifest(), GREET, {"who": "x"}, no_shell).kind == "unavailable"


def test_profile_matches_the_manifest(tmp_path: Path, ts: Settings) -> None:
    out = tmp_path / "out"
    offline = executor.tool_profile(_manifest(write_dirs=[str(out)]), str(tmp_path / "s"),
                                    str(tmp_path / "c"), ts, home=str(tmp_path))
    assert not offline.network and offline.read_roots is None and offline.no_fork
    assert executor.sandbox.canon(out) in offline.write_roots
    assert executor.sandbox.canon(os.environ["AETHER_DATA_DIR"]) in offline.protected_read
    online = executor.tool_profile(_manifest(network=True, read_dirs=[str(tmp_path / "in")]),
                                   str(tmp_path / "s"), str(tmp_path / "c"), ts,
                                   home=str(tmp_path))
    assert online.network and online.read_roots is not None
    assert executor.sandbox.canon(tmp_path / "in") in online.read_roots
    assert executor.sandbox.canon(tmp_path / "c") in online.read_roots
    assert "/System" in online.read_roots
    text, params = online.render()
    assert "(deny process-fork)" in text and "(deny process-exec)" in text
    assert executor.sandbox.canon(os.path.dirname(os.__file__)) in online.read_roots
    assert set(executor.python_exec_roots()) <= set(params.values())
    # credential stores stay unreadable even inside the readable roots
    assert text.index("(deny file-read-data)") < text.rindex("(deny file-read-data\n")
    assert any(v == executor.sandbox.canon(tmp_path / "in") for v in params.values())
    no_net = Settings(**{**ts.__dict__, "network_cap": False})
    assert not executor.tool_profile(_manifest(network=True), "/tmp/s", "/tmp/c", no_net).network


# ---- store -----------------------------------------------------------------------------------

def test_store_versions_history_rollback_and_remove(ts: Settings) -> None:
    v1 = store.install(_manifest(), GREET)
    assert v1.manifest.version == 1 and store.load("my_greet").code() == GREET
    v2 = store.install(_manifest(), GREET.replace("hi", "hello"))
    assert v2.manifest.version == 2 and store.versions("my_greet") == [1]
    assert [t.name for t in store.list_tools()] == ["my_greet"]
    back = store.rollback("my_greet")
    assert back.manifest.version == 1 and "hi" in back.code()
    assert store.versions("my_greet") == [2]
    assert store.install(_manifest(), GREET).manifest.version == 3
    (store.tools_root() / "my_greet" / "tool.py").write_text("def run(args):\n    return 'x'\n")
    assert store.load("my_greet") is None           # changed outside Aether
    assert store.load("../etc") is None and store.load("rm") is None
    assert store.remove("my_greet") and store.load("my_greet") is None
    assert any((store.tools_root() / ".removed").iterdir())
    assert not store.remove("my_greet")


# ---- generation and review -------------------------------------------------------------------

def test_extract_code_and_verdicts() -> None:
    assert generate.extract_code("Here:\n```python\ndef run(args):\n    return 'a'\n```") \
        == "def run(args):\n    return 'a'\n"
    assert generate.extract_code("def run(args):\n    return 1").startswith("def run")
    assert generate.extract_code("I can't do that.") == ""
    assert generate.extract_code("```python\n" + "x = 1\n" * 5000 + "```") == ""
    assert generate.parse_verdict('{"verdict": "approve", "reasons": ["fine"]}').approved
    assert generate.parse_verdict('```json\n{"verdict": "Approve"}\n```').approved
    assert generate.parse_verdict('Sure! {"verdict": "approve"} done').approved
    for text in ("approve", '{"verdict": "approve!"}', '["approve"]', "", "{bad json",
                 '{"verdict": "reject", "reasons": ["reads ~/.ssh"]}'):
        assert not generate.parse_verdict(text).approved, text
    assert generate.parse_verdict('{"verdict": "reject", "reasons": ["reads ~/.ssh"]}') \
        .reasons == ["reads ~/.ssh"]


def test_build_retries_checker_failures_but_not_review_rejections() -> None:
    client = Scripted(["import subprocess\ndef run(args):\n    return ''\n", GREET])
    res = generate.build(client, _manifest(), "say hi")
    assert res.ok and res.attempts == 2 and res.code == GREET
    assert "rejected by the checker" in client.calls[1][1]
    assert "import subprocess is not allowed" in client.calls[1][1]
    assert "Rules:" in client.calls[0][0] and "no internet" in client.calls[0][0]
    judged = client.calls[2]
    assert judged[0] == generate.JUDGE_SYSTEM and '"name": "my_greet"' in judged[1]
    assert "say hi" not in judged[1]               # the reviewer never sees the request

    rejecting = Scripted([GREET, GREET], ['{"verdict": "reject", "reasons": ["sneaky"]}'])
    res = generate.build(rejecting, _manifest(), "say hi")
    assert not res.ok and res.problems == ["sneaky"] and len(rejecting.calls) == 2

    class Broken(Scripted):
        def step(self, system, messages, tools, *, abort_event=None):  # noqa: ANN001, ANN201
            if system == generate.JUDGE_SYSTEM:
                raise RuntimeError("503")
            return super().step(system, messages, tools)

    assert not generate.build(Broken([GREET]), _manifest(), "x").ok


def test_repair_prompt_carries_the_failure() -> None:
    client = Scripted([GREET])
    failure = generate.failure_report(
        executor.RunResult(False, "error", "NameError: name 'nme' is not defined",
                           traceback="Traceback …"), {"who": "Ann"})
    res = generate.build(client, _manifest(), "say hi", previous_code="def run(args): nme",
                         failure=failure)
    assert res.ok
    prompt = client.calls[0][1]
    assert "The current version failed" in prompt and "nme" in prompt and '"who": "Ann"' in prompt


# ---- registry and policy ---------------------------------------------------------------------

def test_make_tool_args_become_a_manifest() -> None:
    m = toolsmith_tools.manifest_from_args({
        "name": "Merge CSVs", "description": "Merge  csv\nfiles.", "params": '{"folder": "string"}',
        "how": "read all", "network": False, "write_dirs": ["~/Out", " "]})
    assert m.name == "my_merge_csvs" and m.description == "Merge csv files."
    assert m.params == {"folder": {"type": "string", "description": ""}}
    assert m.required == ["folder"] and m.write_dirs == ["~/Out"]
    text = toolsmith_tools.approval_text(m, replacing=False)
    assert "Create a new tool: my_merge_csvs" in text and "can write in ~/Out" in text
    assert toolsmith_tools.describe("make_tool", {"name": "x y"}) == "write a new tool: my_x_y"
    assert DEFAULT_REGISTRY.get("make_tool") is not None


def test_policy_treats_self_written_tools_as_code() -> None:
    spec = ToolSpec("my_x", {"type": "object", "properties": {}}, "shell", "read")
    p = Policy(PolicyConfig())
    assert p.impact_of(spec, {}) == "reversible"
    assert not p.requires_confirm(spec, {})
    assert p.is_rule_of_two_risk(spec, {}, True, FocusState())
    assert Policy(PolicyConfig(careful=True)).requires_confirm(spec, {})
    assert "run your self-written tool my_x" in p.describe_operation(spec, {"a": 1})


# ---- the agent loop ------------------------------------------------------------------------

@pytest.fixture
def agent(ts: Settings, minimal_config, tmp_path: Path):  # noqa: ANN001, ANN201
    from aether.core.orchestrator import Agent

    minimal_config.raw["toolsmith"] = {"allow_unsandboxed": True, "timeout_s": 5}
    minimal_config.raw["policy"] = {"approved_file_roots": [str(tmp_path)]}
    a = Agent(minimal_config, hud=None)
    a.asked = []

    async def confirm(text: str) -> bool:
        a.asked.append(text)
        return a.approve

    a.approve = True
    a.confirm_async = confirm
    return a


def _call(agent, name: str, args: dict):  # noqa: ANN001, ANN202
    return asyncio.run(agent._execute_call(name, args, step=1, rid="r1"))  # noqa: SLF001


MAKE = {"name": "greet", "description": "Greets someone.", "how": "return hi <who>",
        "params": {"who": {"type": "string"}}, "required": ["who"]}


def test_make_tool_asks_then_installs_and_runs(agent, monkeypatch) -> None:  # noqa: ANN001
    client = Scripted([GREET])
    monkeypatch.setattr(agent, "_toolsmith_client", lambda: client)
    out = _call(agent, "make_tool", MAKE)
    assert not out.error and "Installed my_greet(who: string)" in out.content
    assert "Create a new tool: my_greet" in agent.asked[0] and "no internet" in agent.asked[0]
    assert agent.registry.get("my_greet") is not None
    assert any(s["name"] == "make_tool" for s in agent._schemas())  # noqa: SLF001
    assert _call(agent, "my_greet", {"who": "Ann"}).content == "hi Ann"


def test_make_tool_declined_invalid_or_rejected(agent, monkeypatch) -> None:  # noqa: ANN001
    agent.approve = False
    assert "declined" in _call(agent, "make_tool", MAKE).content
    assert store.load("my_greet") is None
    agent.approve = True
    bad = _call(agent, "make_tool", {**MAKE, "write_dirs": ["/etc"]})
    assert bad.error and "outside the approved folders" in bad.content and len(agent.asked) == 1
    assert "'how' is required" in _call(agent, "make_tool", {**MAKE, "how": ""}).content
    monkeypatch.setattr(agent, "_toolsmith_client", lambda: Scripted(
        [GREET], ['{"verdict": "reject", "reasons": ["does more than it says"]}']))
    out = _call(agent, "make_tool", MAKE)
    assert out.error and "does more than it says" in out.content
    assert store.load("my_greet") is None
    agent.world.untrusted_seen = True
    monkeypatch.setattr(agent, "_toolsmith_client", lambda: Scripted([GREET]))
    _call(agent, "make_tool", MAKE)
    assert agent.asked[-1].startswith("⚠️ This run read untrusted content")


def test_make_tool_off_or_unavailable(agent) -> None:  # noqa: ANN001
    agent.toolsmith.allow_unsandboxed = False
    assert "sandbox" in _call(agent, "make_tool", MAKE).content
    agent.toolsmith.enabled = False
    assert "turned off" in _call(agent, "make_tool", MAKE).content
    assert all(s["name"] != "make_tool" for s in agent._schemas())  # noqa: SLF001


BUGGY = "def run(args):\n    return 'hi ' + nme\n"


def _install(agent, code: str) -> None:  # noqa: ANN001
    tool = store.install(_manifest(), code)
    agent.registry.register(toolsmith_tools.spec_for(tool, agent.toolsmith))


def test_a_failing_tool_is_repaired_with_the_same_manifest(agent, monkeypatch) -> None:  # noqa: ANN001
    _install(agent, BUGGY)
    client = Scripted([GREET])
    monkeypatch.setattr(agent, "_toolsmith_client", lambda: client)
    out = _call(agent, "my_greet", {"who": "Ann"})
    assert out.content == "hi Ann" and not out.error
    fixed = store.load("my_greet")
    assert fixed.manifest.version == 2 and fixed.manifest.capabilities() == \
        _manifest().capabilities()
    assert "NameError" in client.calls[0][1] and agent._tool_repairs == {"my_greet": 1}  # noqa: SLF001


def test_repairs_are_capped(agent, monkeypatch) -> None:  # noqa: ANN001
    _install(agent, BUGGY)
    monkeypatch.setattr(agent, "_toolsmith_client", lambda: Scripted([BUGGY, BUGGY, BUGGY]))
    out = _call(agent, "my_greet", {"who": "Ann"})
    assert out.error and "repaired 2 time(s)" in out.content
    assert store.load("my_greet").manifest.version == 3


def test_no_repair_for_bad_input_or_untrusted_runs(agent, monkeypatch) -> None:  # noqa: ANN001
    _install(agent, "def run(args):\n    return open(args['who']).read()\n")
    monkeypatch.setattr(agent, "_toolsmith_client", lambda: pytest.fail("no repair expected"))
    out = _call(agent, "my_greet", {"who": "/nope/file"})
    assert "Check the arguments" in out.content
    _install(agent, BUGGY)
    agent.world.untrusted_seen = True
    out = _call(agent, "my_greet", {"who": "Ann"})
    assert "untrusted content" in out.content
    assert "run your self-written tool my_greet" in agent.asked[-1]   # Rule of Two asked first


# ---- sidecar -----------------------------------------------------------------------------------

def test_toolsmith_endpoints(ts: Settings, sidecar_client) -> None:  # noqa: ANN001
    store.install(_manifest(), GREET)
    listed = sidecar_client.get("/toolsmith/tools").json()
    assert [t["name"] for t in listed["tools"]] == ["my_greet"]
    assert listed["tools"][0]["signature"] == "my_greet(who: string)"
    detail = sidecar_client.get("/toolsmith/tools/my_greet").json()
    assert detail["code"] == GREET and detail["how"] == "return hi <who>"
    assert sidecar_client.post("/toolsmith/tools/my_greet/rollback").status_code == 409
    store.install(_manifest(), GREET.replace("hi", "hello"))
    back = sidecar_client.post("/toolsmith/tools/my_greet/rollback").json()
    assert back["version"] == 1
    assert sidecar_client.delete("/toolsmith/tools/my_greet").json() == {"removed": "my_greet"}
    assert sidecar_client.get("/toolsmith/tools/my_greet").status_code == 404
