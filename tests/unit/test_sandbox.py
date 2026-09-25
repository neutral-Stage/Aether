"""Seatbelt profiles: rendering, roots, protected paths, env scrubbing, wiring."""
from __future__ import annotations

import os
import subprocess

import pytest

from aether.effectors import sandbox, shell


def _settings(**kw) -> dict:  # noqa: ANN003
    base = {"enabled": True, "shell": True, "coders": True, "shell_network": True,
            "coder_network": True, "protect_self": True, "_roots": ["~"], "_network_cap": True}
    base.update(kw)
    return base


def test_render_uses_params_not_spliced_paths(tmp_path) -> None:  # noqa: ANN001
    evil = str(tmp_path / 'a") (allow file-write* (subpath "/')
    prof = sandbox.Profile("t", (evil,), (str(tmp_path / "p"),), (str(tmp_path / "s"),), False)
    text, params = prof.render()
    assert evil not in text and params["W_0"] == evil
    assert text.index("(deny file-write*)") < text.index('(subpath (param "W_0"))') \
        < text.index('(subpath (param "PW_0"))')
    assert "(deny network*)" in text
    assert "(deny file-read-data" in text and '(param "PR_0")' in text
    assert "(deny network*)" not in sandbox.Profile("t", (), network=True).render()[0]


def test_wrap_builds_sandbox_exec_argv(tmp_path) -> None:  # noqa: ANN001
    prof = sandbox.Profile("t", (str(tmp_path),), network=True)
    argv = prof.wrap(["/bin/sh", "-c", "echo hi"])
    assert argv[0] == sandbox.SANDBOX_EXEC
    assert argv[1:3] == ["-D", f"W_0={tmp_path}"]
    i = argv.index("-p")
    assert argv[i + 1].startswith("; Aether sandbox: t") and argv[i + 2:] == ["/bin/sh", "-c", "echo hi"]


def test_shell_profile_roots_and_protection(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    home = str(tmp_path / "home")
    root = tmp_path / "work"
    prof = sandbox.shell_profile(_settings(_roots=[str(root)], extra_write_roots=["/opt/x"]),
                                 home=home)
    assert sandbox.canon(root) in prof.write_roots and "/opt/x" in prof.write_roots
    assert any(r.endswith("Library/Caches") for r in prof.write_roots)
    assert sandbox.canon(os.path.join(home, ".zshrc")) in prof.protected_write
    assert sandbox.canon(os.path.join(home, "Library/LaunchAgents")) in prof.protected_write
    assert sandbox.canon(os.path.join(home, ".ssh")) in prof.protected_read
    from aether.core.paths import data_dir

    assert sandbox.canon(data_dir()) in prof.protected_write
    assert sandbox.canon(data_dir() / ".audit_hmac_key") in prof.protected_read
    unprotected = sandbox.shell_profile(_settings(protect_self=False), home=home)
    assert sandbox.canon(data_dir()) not in unprotected.protected_write


@pytest.mark.parametrize(("shell_net", "cap", "expect"), [
    (True, True, True), (False, True, False), (True, False, False)])
def test_shell_network_follows_setting_and_capability(shell_net, cap, expect) -> None:  # noqa: ANN001
    s = _settings(shell_network=shell_net, _network_cap=cap)
    assert sandbox.shell_profile(s, home="/Users/x").network is expect


def test_coder_profile_includes_worktree_git_dir(tmp_path) -> None:  # noqa: ANN001
    repo_git = tmp_path / "repo" / ".git"
    (repo_git / "worktrees" / "wt1").mkdir(parents=True)
    wt = tmp_path / "wt1"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {repo_git / 'worktrees' / 'wt1'}\n")
    assert sandbox.git_common_dir(str(wt)) == sandbox.canon(repo_git)
    prof = sandbox.coder_profile(str(wt), _settings(), home=str(tmp_path / "h"))
    assert prof.write_roots[:2] == (sandbox.canon(wt), sandbox.canon(repo_git))
    assert any(r.endswith(".claude") for r in prof.write_roots)
    assert sandbox.git_common_dir(str(tmp_path)) is None


@pytest.mark.parametrize(("argv", "own"), [
    (["codex", "exec", "--json", "hi"], True),
    (["codex", "exec", "--sandbox", "workspace-write", "hi"], True),
    (["codex", "exec", "--sandbox", "danger-full-access", "hi"], False),
    (["codex", "exec", "--dangerously-bypass-approvals-and-sandbox", "hi"], False),
    (["claude", "-p", "hi"], False), ([], False),
])
def test_self_sandboxed(argv, own) -> None:  # noqa: ANN001
    assert sandbox.self_sandboxed(argv) is own


def test_wrap_coder_skips_codex_and_disabled(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.setattr(sandbox, "available", lambda: True)
    argv, prof = sandbox.wrap_coder(["claude", "-p", "x"], str(tmp_path), settings=_settings())
    assert argv[0] == sandbox.SANDBOX_EXEC and prof is not None and prof.name == "coder"
    argv, prof = sandbox.wrap_coder(["codex", "exec", "x"], str(tmp_path), settings=_settings())
    assert argv == ["codex", "exec", "x"] and prof is None
    argv, prof = sandbox.wrap_coder(["claude"], str(tmp_path), settings=_settings(coders=False))
    assert prof is None
    monkeypatch.setattr(sandbox, "available", lambda: False)
    assert sandbox.wrap_shell("ls", settings=_settings())[1] is None


def test_child_env_drops_aether_secrets() -> None:
    env = sandbox.child_env({
        "PATH": "/bin", "HOME": "/h", "AETHER_SIDECAR_TOKEN": "t", "ZAI_API_KEY": "k",
        "GITHUB_TOKEN": "g", "AWS_SECRET_ACCESS_KEY": "s", "SSH_AUTH_SOCK": "/s",
        "AETHER_SPAWN_DEPTH": "1", "LANG": "en_US.UTF-8"})
    assert env == {"PATH": "/bin", "HOME": "/h", "SSH_AUTH_SOCK": "/s",
                   "AETHER_SPAWN_DEPTH": "1", "LANG": "en_US.UTF-8"}


def test_explain_denial() -> None:
    prof = sandbox.Profile("shell", ("/x",), network=False)
    assert "blocked" in sandbox.explain_denial("touch: /etc/x: Operation not permitted", prof)
    assert "network is off" in sandbox.explain_denial("curl: (6) Could not resolve host: a", prof)
    assert "inside another" in sandbox.explain_denial("sandbox-exec: sandbox_apply: x", prof)
    assert sandbox.explain_denial("Operation not permitted", None) is None
    assert sandbox.explain_denial("syntax error", prof) is None


def test_shell_run_wraps_scrubs_and_explains(monkeypatch) -> None:
    seen = {}

    def fake_run(argv, **kw):  # noqa: ANN001, ANN003, ANN202
        seen["argv"], seen["env"] = argv, kw["env"]
        return subprocess.CompletedProcess(argv, 1, "", "rm: x: Operation not permitted")

    prof = sandbox.Profile("shell", ("/x",))
    monkeypatch.setattr(sandbox, "wrap_shell", lambda cmd: (prof.wrap(["/bin/sh", "-c", cmd]), prof))
    monkeypatch.setattr(shell.subprocess, "run", fake_run)
    monkeypatch.setenv("AETHER_SIDECAR_TOKEN", "secret")
    out = shell.run("rm -f /etc/x").summary()
    assert seen["argv"][0] == sandbox.SANDBOX_EXEC and seen["argv"][-1] == "rm -f /etc/x"
    assert "AETHER_SIDECAR_TOKEN" not in seen["env"]
    assert "sandbox blocked" in out
    shell.run("true", sandboxed=False)
    assert seen["argv"] == ["/bin/sh", "-c", "true"]


def test_fleet_and_gate_spawn_through_sandbox(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    from aether.fleet import graph_runner
    from aether.fleet.graph import TaskNode

    calls = []
    monkeypatch.setattr(sandbox, "wrap_coder",
                        lambda argv, ws, **k: (["SANDBOX", ws, *argv], None))
    monkeypatch.setattr(graph_runner.subprocess, "run",
                        lambda argv, **kw: calls.append(argv) or
                        subprocess.CompletedProcess(argv, 0, "", ""))
    node = TaskNode(id="a", title="a", prompt="p", paths=["a"], gate_cmd="make test")
    assert graph_runner.run_gate(node, str(tmp_path))[0]
    assert calls[0] == ["SANDBOX", str(tmp_path), "/bin/sh", "-c", "make test"]
