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


def test_finder_go_to_expands_tilde_and_opens_when_no_windows(monkeypatch) -> None:  # noqa: ANN001
    import os

    sources = []
    monkeypatch.setattr(
        applescript.subprocess,
        "run",
        lambda cmd, **kw: sources.append(cmd[2]) or SimpleNamespace(  # noqa: ARG005
            returncode=0, stdout="/Users/test/Downloads\n", stderr=""
        ),
    )
    out = applescript.finder_go_to("~/Downloads")
    assert out == "/Users/test/Downloads"
    assert os.path.expanduser("~/Downloads") in sources[0]
    assert "~/Downloads" not in sources[0]
    assert "count of Finder windows" in sources[0]


def test_type_text_dispatches_return_and_tab_keys(monkeypatch) -> None:  # noqa: ANN001
    from aether.effectors import input as kbd

    pressed: list[str] = []
    typed: list[str] = []
    monkeypatch.setattr(kbd, "_OK", True)
    monkeypatch.setattr(kbd, "press_key", lambda k, modifiers=None: pressed.append(k))  # noqa: ARG005
    monkeypatch.setattr(kbd, "CGEventCreateKeyboardEvent", lambda *a: object())
    monkeypatch.setattr(
        kbd, "CGEventKeyboardSetUnicodeString", lambda ev, n, ch: typed.append(ch)  # noqa: ARG005
    )
    monkeypatch.setattr(kbd, "_post", lambda ev: None)
    kbd.type_text("A\r\nB\tC\nD", per_char_delay=0)
    assert pressed == ["return", "tab", "return"]
    assert typed == ["A", "A", "B", "B", "C", "C", "D", "D"]


def test_ocr_image_dimensions_without_extent(tmp_path) -> None:  # noqa: ANN001
    import struct
    import zlib
    from aether.perception import ocr

    def make_png(w: int, h: int) -> bytes:
        def chunk(tag: bytes, data: bytes) -> bytes:
            crc = zlib.crc32(tag + data) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

        ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
        idat = zlib.compress(b"\x00" + b"\x00\x00\x00" * w)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")

    png = tmp_path / "sample.png"
    png.write_bytes(make_png(320, 240))
    assert ocr.image_dimensions(str(png)) == (320, 240)


def test_check_update_semver_and_feed_url(monkeypatch) -> None:  # noqa: ANN001
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "check_update", Path(__file__).resolve().parents[2] / "scripts" / "check_update.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.is_newer("1.0.0", "1.0.0-rc.1") is True
    assert mod.is_newer("1.0.0-rc.1", "1.0.0") is False
    assert mod.is_newer("1.0.0-rc.2", "1.0.0-rc.1") is True
    assert mod.is_newer("1.0.0-rc.1", "1.0.0-rc.1") is False
    assert mod.is_newer("1.0.1", "1.0.0") is True

    monkeypatch.delenv("AETHER_UPDATE_FEED_URL", raising=False)
    monkeypatch.delenv("AETHER_GITHUB_REPO", raising=False)
    assert mod.resolve_feed_url() == "https://api.github.com/repos/neutral-Stage/Aether/releases/latest"
