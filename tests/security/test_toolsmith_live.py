"""Self-written tools under the real macOS sandbox (skipped where sandbox-exec is missing).

The executor runs the code as given, so these tests skip the static check on
purpose: they prove the sandbox, not the checker, is the boundary.
"""
from __future__ import annotations

import socket
import threading
from pathlib import Path

import pytest

from aether.effectors import sandbox
from aether.toolsmith import executor
from aether.toolsmith.manifest import ToolManifest
from aether.toolsmith.settings import Settings

pytestmark = [pytest.mark.security,
              pytest.mark.skipif(not sandbox.available(), reason="needs macOS sandbox-exec")]


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    data = tmp_path / "data"
    data.mkdir()
    (data / "memory.db").write_text("PRIVATE MEMORY")
    monkeypatch.setenv("AETHER_DATA_DIR", str(data))
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "key").write_text("SECRET")
    for d in ("allowed", "other", "out"):
        (tmp_path / d).mkdir()
    (tmp_path / "allowed" / "a.txt").write_text("A")
    (tmp_path / "other" / "b.txt").write_text("B")
    settings = Settings(approved_roots=[str(tmp_path)], timeout_s=30,
                        sandbox={"extra_deny_read": [str(secrets)]})
    return tmp_path, settings


def _m(**kw) -> ToolManifest:  # noqa: ANN003
    return ToolManifest("my_probe", "probe", {"p": {"type": "string"}}, [], **kw)


def _run(settings: Settings, body: str, manifest: ToolManifest | None = None, **args):  # noqa: ANN003, ANN202
    code = "def run(args):\n" + "\n".join("    " + ln for ln in body.strip().splitlines()) + "\n"
    return executor.run_tool(manifest or _m(), code, args, settings)


def _listener() -> tuple[socket.socket, int]:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def accept() -> None:
        try:
            srv.accept()
        except OSError:        # closed by the test, or the connection was refused
            pass

    threading.Thread(target=accept, daemon=True).start()
    return srv, srv.getsockname()[1]


def test_tool_runs_sandboxed_with_a_private_scratch_folder(env) -> None:  # noqa: ANN001
    _, settings = env
    r = _run(settings, """
import json, os, tempfile
p = os.path.join(tempfile.gettempdir(), 'x.txt')
open(p, 'w').write('ok')
return json.dumps({'sum': sum(range(10)), 'wrote': open(p).read()})
""")
    assert r.ok and r.sandboxed, (r.kind, r.output)
    assert r.output == '{"sum": 45, "wrote": "ok"}'


def test_writes_only_in_declared_folders(env) -> None:  # noqa: ANN001
    tmp, settings = env
    r = _run(settings, f"open({str(tmp / 'other' / 'new.txt')!r}, 'w').write('x')")
    assert r.kind == "denied", (r.kind, r.output)
    assert not (tmp / "other" / "new.txt").exists()
    ok = _run(settings, f"open({str(tmp / 'out' / 'new.txt')!r}, 'w').write('x')\nreturn 'w'",
              _m(write_dirs=[str(tmp / "out")]))
    assert ok.ok and (tmp / "out" / "new.txt").read_text() == "x"


def test_no_network_unless_declared(env) -> None:  # noqa: ANN001
    _, settings = env
    srv, port = _listener()
    try:
        r = _run(settings, f"import socket\nsocket.create_connection(('127.0.0.1', {port}), 3)")
        assert not r.ok and r.kind == "denied", (r.kind, r.output)
    finally:
        srv.close()


def test_no_child_processes(env) -> None:  # noqa: ANN001
    tmp, settings = env
    marker = tmp / "out" / "spawned"
    r = _run(settings, f"import subprocess\nsubprocess.run(['/usr/bin/touch', {str(marker)!r}])",
             _m(write_dirs=[str(tmp / "out")]))
    assert not r.ok, r.output
    assert not marker.exists()


def test_no_spawning_other_programs(env) -> None:  # noqa: ANN001
    tmp, settings = env
    marker = tmp / "out" / "spawned"
    r = _run(settings, f"""
import os
pid = os.posix_spawn('/usr/bin/touch', ['touch', {str(marker)!r}], {{}})
os.waitpid(pid, 0)
return 'spawned'
""", _m(write_dirs=[str(tmp / "out")]))
    assert not marker.exists(), (r.kind, r.output)
    assert not r.ok


def test_credentials_and_aether_data_unreadable(env) -> None:  # noqa: ANN001
    tmp, settings = env
    assert _run(settings, f"return open({str(tmp / 'secrets' / 'key')!r}).read()").kind == "denied"
    data = tmp / "data" / "memory.db"
    assert _run(settings, f"return open({str(data)!r}).read()").kind == "denied"
    assert _run(settings, f"return open({str(tmp / 'other' / 'b.txt')!r}).read()").output == "B"


def test_network_tool_reads_only_its_folders(env) -> None:  # noqa: ANN001
    tmp, settings = env
    online = _m(network=True, read_dirs=[str(tmp / "allowed")])
    ok = _run(settings, f"return open({str(tmp / 'allowed' / 'a.txt')!r}).read()", online)
    assert ok.ok and ok.output == "A", (ok.kind, ok.output)      # Python starts under the profile
    denied = _run(settings, f"return open({str(tmp / 'other' / 'b.txt')!r}).read()", online)
    assert denied.kind == "denied", (denied.kind, denied.output)
    srv, port = _listener()
    try:
        net = _run(settings, f"import socket\nsocket.create_connection(('127.0.0.1', {port}), 3)"
                   "\nreturn 'connected'", online)
        assert net.ok and net.output == "connected", (net.kind, net.output)
    finally:
        srv.close()
