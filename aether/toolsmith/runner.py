"""Runs one self-written tool inside its sandboxed child process.

Standard library only; never imports Aether. The executor starts it as

    python -I -S -B runner.py <tool.py> <cpu seconds>

with a JSON request ``{"nonce": …, "args": {…}}`` on stdin. The runner reads
the request before the tool's code is loaded, so the tool never sees the
nonce. It prints one line, ``<nonce>{"ok": …}``, as the last line of stdout;
anything the tool prints is captured into ``log`` instead.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import traceback

MAX_RESULT = 20_000
MAX_LOG = 2_000


def _limits(cpu_seconds: int) -> None:
    try:
        import resource
    except ImportError:   # not on macOS/Linux
        return
    wanted = (
        (resource.RLIMIT_CPU, cpu_seconds),
        (resource.RLIMIT_FSIZE, 256 * 1024 * 1024),
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_NOFILE, 256),
    )
    for which, value in wanted:
        try:
            _soft, hard = resource.getrlimit(which)
            if hard != resource.RLIM_INFINITY:
                value = min(value, hard)
            resource.setrlimit(which, (value, value))
        except (ValueError, OSError):
            pass


def _as_text(result: object) -> str:
    if isinstance(result, str):
        return result
    if result is None:
        return ""
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)


def main(argv: list[str]) -> int:
    out = sys.stdout
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        request = {}
    nonce = str(request.get("nonce") or "")
    args = request.get("args") if isinstance(request.get("args"), dict) else {}

    def emit(payload: dict) -> None:
        out.write("\n" + nonce + json.dumps(payload, ensure_ascii=False) + "\n")
        out.flush()

    if len(argv) < 2:
        emit({"ok": False, "error": "runner: no tool given"})
        return 2
    cpu = int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else 60
    _limits(cpu)
    log = io.StringIO()
    try:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            spec = importlib.util.spec_from_file_location("aether_user_tool", argv[1])
            if spec is None or spec.loader is None:
                raise ImportError("cannot load the tool")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            run = getattr(module, "run", None)
            if not callable(run):
                raise TypeError("the tool has no run(args) function")
            result = run(args)
    except BaseException as e:  # noqa: BLE001 — report everything, even SystemExit
        emit({"ok": False, "error": f"{type(e).__name__}: {e}"[:1000],
              "error_type": type(e).__name__,
              "traceback": traceback.format_exc(limit=8)[-3000:],
              "log": log.getvalue()[-MAX_LOG:]})
        return 1
    text = _as_text(result)
    emit({"ok": True, "result": text[:MAX_RESULT], "truncated": len(text) > MAX_RESULT,
          "log": log.getvalue()[-MAX_LOG:]})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
