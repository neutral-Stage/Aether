"""aether/effectors/applescript.py: argv-passing runs, string escaping."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from aether.effectors import applescript


def test_run_applescript_args_builds_argv_command(monkeypatch) -> None:  # noqa: ANN001
    calls = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN202
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stdout="normal\n", stderr="")

    monkeypatch.setattr(applescript.subprocess, "run", fake_run)
    res = applescript.run_applescript_args("on run argv\nend run", ["com.google.Chrome"],
                                           timeout=3)
    assert res == applescript.AppleScriptResult(0, "normal\n", "")
    cmd, kwargs = calls[0]
    assert cmd == ["osascript", "-e", "on run argv\nend run", "com.google.Chrome"]
    assert kwargs["timeout"] == 3
    assert kwargs["capture_output"] and kwargs["text"]


def test_run_applescript_args_passes_multiple_args_without_splicing(monkeypatch) -> None:  # noqa: ANN001
    calls = []
    monkeypatch.setattr(applescript.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(  # noqa: ARG005
                            returncode=0, stdout="", stderr=""))
    applescript.run_applescript_args("on run argv\nend run", ['a "quoted" value', "b"])
    assert calls[0][-2:] == ['a "quoted" value', "b"]     # never re-quoted or altered


def test_run_applescript_args_never_raises_on_timeout(monkeypatch) -> None:  # noqa: ANN001
    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN202
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 10))

    monkeypatch.setattr(applescript.subprocess, "run", fake_run)
    res = applescript.run_applescript_args("on run argv\nend run", ["x"], timeout=5)
    assert res.returncode == 124 and "5s" in res.stderr


def test_run_applescript_args_never_raises_on_other_errors(monkeypatch) -> None:  # noqa: ANN001
    def fake_run(cmd, **kwargs):  # noqa: ANN001, ARG001
        raise OSError("osascript not found")

    monkeypatch.setattr(applescript.subprocess, "run", fake_run)
    res = applescript.run_applescript_args("on run argv\nend run", ["x"])
    assert res.returncode == 1 and "osascript not found" in res.stderr


@pytest.mark.parametrize(("raw", "want"), [
    ("hello", '"hello"'),
    ('say "hi"', r'"say \"hi\""'),
    ("back\\slash", r'"back\\slash"'),
    ("line\nbreak", r'"line\nbreak"'),
    ('mix "quote" and \\ and \n', r'"mix \"quote\" and \\ and \n"'),
])
def test_as_applescript_string_escapes(raw, want) -> None:  # noqa: ANN001
    assert applescript.as_applescript_string(raw) == want


def test_finder_safari_mail_scripts_use_the_escaper(monkeypatch) -> None:  # noqa: ANN001
    sources = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN202, ARG001
        sources.append(cmd[2])
        return SimpleNamespace(returncode=0, stdout="OK", stderr="")

    monkeypatch.setattr(applescript.subprocess, "run", fake_run)
    applescript.finder_go_to('/tmp/a "weird" path')
    applescript.safari_open_url('https://example.com/"x"')
    applescript.mail_compose(to="a@example.com", subject='Re: "hi"', body="line1\nline2")
    assert applescript.as_applescript_string('/tmp/a "weird" path') in sources[0]
    assert applescript.as_applescript_string('https://example.com/"x"') in sources[1]
    assert applescript.as_applescript_string('Re: "hi"') in sources[2]
    assert applescript.as_applescript_string("line1\nline2") in sources[2]
