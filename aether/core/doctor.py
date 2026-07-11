"""`aether doctor` — first-run environment preflight (Phase 12).

Everything in Aether is heavily tested, but it has real external dependencies
(API keys, coding CLIs, macOS TCC permissions, git). This reports exactly what's
present vs missing BEFORE the first real run, so a failure has a diagnosis
instead of a mystery. Each check is a small pure-ish function so it's testable.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Callable

OK, WARN, FAIL = "ok", "warn", "fail"

# Cloud providers whose key unlocks the frontier/vision tiers.
_PROVIDER_ENVS = [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY",
    "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "ZAI_API_KEY",
]
# Coding CLIs the fleet / delegate_to_coder can drive.
_CODING_CLIS = ["claude", "codex", "opencode", "cursor"]


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str = ""
    fix: str = ""


def _env_keys_present() -> list[str]:
    import os
    return [k for k in _PROVIDER_ENVS if os.environ.get(k, "").strip()]


def check_python() -> Check:
    import sys
    v = sys.version_info
    if v < (3, 11):
        return Check("Python ≥ 3.11", FAIL,
                     f"found {v.major}.{v.minor}.{v.micro}",
                     "use python3.11 (python3 may be too old)")
    return Check("Python ≥ 3.11", OK, f"{v.major}.{v.minor}.{v.micro}")


def check_sidecar_deps() -> Check:
    missing = [m for m in ("fastapi", "uvicorn", "httpx", "yaml")
               if not _importable(m)]
    if missing:
        return Check("Sidecar deps", FAIL, f"missing: {', '.join(missing)}",
                     "pip install -r requirements-sidecar.txt")
    return Check("Sidecar deps", OK, "fastapi/uvicorn/httpx/yaml present")


def check_perception_deps() -> Check:
    # pyobjc powers AX perception + effectors; without it, control is dead.
    if _importable("ApplicationServices") and _importable("Quartz"):
        return Check("macOS perception (pyobjc)", OK, "ApplicationServices + Quartz")
    return Check("macOS perception (pyobjc)", FAIL, "pyobjc not importable",
                 "pip install -r requirements.txt (needs macOS)")


def check_llm_backend() -> Check:
    keys = _env_keys_present()
    if keys:
        return Check("LLM backend", OK, f"cloud keys: {', '.join(keys)}")
    if _ollama_up():
        return Check("LLM backend", WARN, "no cloud key, but Ollama is reachable",
                     "set a provider key for the frontier tier, or run --local-only")
    return Check("LLM backend", FAIL,
                 "no cloud API key in this env and Ollama not reachable",
                 "export a provider key (e.g. ANTHROPIC_API_KEY) for CLI use, or "
                 "enter it in the app's Settings → API Keys (Keychain), or start Ollama")


def check_coding_clis() -> Check:
    found = [c for c in _CODING_CLIS if shutil.which(c)]
    if found:
        return Check("Coding CLIs (fleet)", OK, f"on PATH: {', '.join(found)}")
    return Check("Coding CLIs (fleet)", WARN,
                 "none of claude/codex/opencode/cursor on PATH",
                 "install a coding CLI to use spawn_agent / delegate_to_coder")


def check_git() -> Check:
    if shutil.which("git"):
        return Check("git", OK, "present")
    return Check("git", WARN, "git not on PATH",
                 "install git — fleet worktree isolation + graph integration need it")


def check_accessibility() -> Check:
    from . import permissions
    try:
        ok = permissions.check_accessibility(prompt=False)
    except Exception:  # noqa: BLE001
        return Check("Accessibility permission", WARN, "could not determine")
    if ok:
        return Check("Accessibility permission", OK, "granted")
    return Check("Accessibility permission", FAIL, "not granted",
                 "System Settings → Privacy & Security → Accessibility → enable "
                 "the app/terminal running Aether (required to see + control apps)")


def check_screen_recording() -> Check:
    from . import permissions
    try:
        ok = permissions.check_screen_recording(request=False)
    except Exception:  # noqa: BLE001
        ok = None
    if ok is True:
        return Check("Screen Recording permission", OK, "granted")
    if ok is None:
        return Check("Screen Recording permission", WARN, "could not determine")
    return Check("Screen Recording permission", WARN, "not granted",
                 "System Settings → Screen Recording — needed for the vision fallback")


def check_config() -> Check:
    try:
        from .config import load_config
        load_config(validate=True)
        return Check("config.yaml valid", OK, "loads + validates")
    except Exception as e:  # noqa: BLE001
        return Check("config.yaml valid", FAIL, str(e)[:120], "fix config.yaml")


# ---- helpers ----

def _importable(mod: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:  # noqa: BLE001
        return False


def _ollama_up(url: str = "http://localhost:11434/api/tags") -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=0.5) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


CHECKS: list[Callable[[], Check]] = [
    check_python, check_sidecar_deps, check_perception_deps, check_llm_backend,
    check_coding_clis, check_git, check_accessibility, check_screen_recording,
    check_config,
]


def run_checks() -> list[Check]:
    out: list[Check] = []
    for fn in CHECKS:
        try:
            out.append(fn())
        except Exception as e:  # noqa: BLE001 — a broken check must not abort doctor
            out.append(Check(fn.__name__, WARN, f"check errored: {e}"))
    return out


def verdict(checks: list[Check]) -> str:
    if any(c.status == FAIL for c in checks):
        return FAIL
    if any(c.status == WARN for c in checks):
        return WARN
    return OK


def format_report(checks: list[Check]) -> str:
    icon = {OK: "✓", WARN: "!", FAIL: "✗"}
    lines = ["Aether preflight (aether doctor):", ""]
    for c in checks:
        line = f"  [{icon[c.status]}] {c.name}" + (f" — {c.detail}" if c.detail else "")
        lines.append(line)
        if c.status != OK and c.fix:
            lines.append(f"        fix: {c.fix}")
    v = verdict(checks)
    summary = {
        OK: "All good — Aether is ready to run.",
        WARN: "Usable, but some capabilities are degraded (see ! above).",
        FAIL: "Not ready — resolve the ✗ items before running.",
    }[v]
    lines += ["", summary]
    return "\n".join(lines)


def main() -> int:
    checks = run_checks()
    print(format_report(checks))
    return 1 if verdict(checks) == FAIL else 0
