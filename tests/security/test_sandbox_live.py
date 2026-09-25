"""Real sandbox-exec runs (macOS only): the profile must parse and actually confine.

Skipped where sandbox-exec is missing (Linux dev boxes); CI runs on macOS.
"""
from __future__ import annotations

import socket
import subprocess
import threading
from pathlib import Path

import pytest

from aether.effectors import sandbox

pytestmark = [pytest.mark.security,
              pytest.mark.skipif(not sandbox.available(), reason="needs macOS sandbox-exec")]


def _sh(prof: sandbox.Profile, script: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(prof.wrap(["/bin/sh", "-c", script]), cwd=cwd,  # noqa: S603
                          capture_output=True, text=True, timeout=30)


@pytest.fixture
def layout(tmp_path):  # noqa: ANN001, ANN201
    root = tmp_path / "root"
    (root / "protected").mkdir(parents=True)
    (root / "repo" / ".git" / "hooks").mkdir(parents=True)
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "id_ed25519").write_text("PRIVATE")
    (secrets / "id_ed25519.pub").write_text("PUBLIC")
    prof = sandbox.Profile(
        "test", (sandbox.canon(root),),
        protected_write=(sandbox.canon(root / "protected"),),
        protected_read=(sandbox.canon(secrets),), network=False)
    return tmp_path, root, secrets, prof


def test_profile_parses_and_runs(layout) -> None:  # noqa: ANN001
    _, root, _, prof = layout
    res = _sh(prof, "echo ok", root)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "ok"


def test_writes_confined_to_roots(layout) -> None:  # noqa: ANN001
    tmp, root, _, prof = layout
    assert _sh(prof, "echo x > inside.txt", root).returncode == 0
    assert (root / "inside.txt").read_text().strip() == "x"
    res = _sh(prof, f"echo x > '{tmp / 'outside.txt'}'", root)
    assert res.returncode != 0 and not (tmp / "outside.txt").exists()
    assert "not permitted" in res.stderr.lower()


def test_protected_paths_inside_roots_stay_read_only(layout) -> None:  # noqa: ANN001
    _, root, _, prof = layout
    assert _sh(prof, "echo x > protected/f", root).returncode != 0
    assert _sh(prof, "echo x > repo/.git/hooks/pre-commit", root).returncode != 0
    assert _sh(prof, "echo x > repo/.git/config", root).returncode != 0
    assert not (root / "repo" / ".git" / "hooks" / "pre-commit").exists()
    # a symlink inside the root does not launder a write to a protected path
    (root / "link").symlink_to(root / "protected")
    assert _sh(prof, "echo x > link/g", root).returncode != 0


def test_credentials_unreadable_but_public_keys_readable(layout) -> None:  # noqa: ANN001
    _, root, secrets, prof = layout
    res = _sh(prof, f"cat '{secrets / 'id_ed25519'}'", root)
    assert res.returncode != 0 and "PRIVATE" not in res.stdout
    res = _sh(prof, f"cat '{secrets / 'id_ed25519.pub'}'", root)
    assert res.returncode == 0 and "PUBLIC" in res.stdout


def test_network_off_blocks_even_loopback(layout) -> None:  # noqa: ANN001
    _, root, _, prof = layout
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    threading.Thread(target=lambda: srv.accept(), daemon=True).start()
    probe = (f"/usr/bin/python3 -c 'import socket; socket.create_connection((\"127.0.0.1\", {port}), 3)'"
             f" || /usr/bin/nc -z -w 3 127.0.0.1 {port}")
    try:
        assert _sh(prof, probe, root).returncode != 0
        on = sandbox.Profile("test-net", prof.write_roots, network=True)
        assert _sh(on, f"/usr/bin/nc -z -w 3 127.0.0.1 {port}", root).returncode == 0
    finally:
        srv.close()


def test_real_shell_profile_runs_everyday_commands(tmp_path) -> None:  # noqa: ANN001
    res = subprocess.run(sandbox.shell_profile().wrap(  # noqa: S603
        ["/bin/sh", "-c", "ls / >/dev/null && date >/dev/null && echo hi > f && cat f"]),
        cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "hi"


def test_cannot_signal_processes_outside_the_sandbox(layout) -> None:  # noqa: ANN001
    _, root, _, prof = layout
    victim = subprocess.Popen(["/bin/sleep", "30"])  # noqa: S603
    try:
        res = _sh(prof, f"kill {victim.pid}", root)
        assert res.returncode != 0
        assert victim.poll() is None
        # its own children are fine
        assert _sh(prof, "sleep 30 & kill $!", root).returncode == 0
    finally:
        victim.kill()


def test_cannot_open_apps_through_launchservices(layout) -> None:  # noqa: ANN001
    _, root, _, prof = layout
    res = _sh(prof, "/usr/bin/open -g -j -a TextEdit", root)
    subprocess.run(["/usr/bin/osascript", "-e", 'quit app "TextEdit"'],  # noqa: S603
                   capture_output=True, timeout=10)
    assert res.returncode != 0


def test_everyday_profile_blocks_apple_events(tmp_path) -> None:  # noqa: ANN001
    script = ["/usr/bin/osascript", "-e", 'tell application "System Events" to get name']
    baseline = subprocess.run(script, capture_output=True, text=True, timeout=20)  # noqa: S603
    if baseline.returncode != 0:
        pytest.skip("Apple Events unavailable here even without the sandbox")
    res = subprocess.run(sandbox.shell_profile().wrap(script), cwd=tmp_path,  # noqa: S603
                         capture_output=True, text=True, timeout=20)
    assert res.returncode != 0
