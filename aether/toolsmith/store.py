"""Installed self-written tools: ``<data dir>/tools/<name>/{tool.py, manifest.json}``.

Every install keeps the previous version under ``history/`` (the last five),
so a bad repair can be rolled back. A tool whose code no longer matches the
hash in its manifest was changed outside Aether and does not load. Removing
a tool moves it to ``tools/.removed/`` instead of deleting it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from ..core.paths import data_dir
from .manifest import PREFIX, ToolManifest

log = logging.getLogger(__name__)

KEEP_HISTORY = 5
_SAFE_NAME = re.compile(rf"^{PREFIX}[a-z][a-z0-9_]{{1,40}}$")


def tools_root() -> Path:
    return data_dir() / "tools"


def sha256(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _tool_dir(name: str) -> Path:
    if not _SAFE_NAME.match(name or ""):
        raise ValueError(f"not a self-written tool name: {name!r}")
    return tools_root() / name


@dataclass
class InstalledTool:
    manifest: ToolManifest
    dir: Path

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def code_path(self) -> Path:
        return self.dir / "tool.py"

    def code(self) -> str:
        return self.code_path.read_text(encoding="utf-8")

    def summary(self) -> dict:
        m = self.manifest
        return {"name": m.name, "description": m.description, "version": m.version,
                "signature": m.signature(), "capabilities": m.capability_lines(),
                "network": m.network, "write_dirs": m.write_dirs, "read_dirs": m.read_dirs,
                "created_at": m.created_at, "code_sha256": m.code_sha256,
                "history": versions(m.name)}


def _read(d: Path) -> InstalledTool | None:
    try:
        manifest = ToolManifest.from_dict(json.loads((d / "manifest.json").read_text("utf-8")))
        code = (d / "tool.py").read_text("utf-8")
    except (OSError, ValueError, TypeError):
        return None
    if not manifest.code_sha256 or manifest.code_sha256 != sha256(code):
        log.warning("self-written tool %s was changed outside Aether; not loading it", d.name)
        return None
    return InstalledTool(manifest, d)


def load(name: str) -> InstalledTool | None:
    try:
        d = _tool_dir(name)
    except ValueError:
        return None
    tool = _read(d) if d.is_dir() else None
    if tool is not None and tool.manifest.name != name:
        return None
    return tool


def list_tools() -> list[InstalledTool]:
    root = tools_root()
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and _SAFE_NAME.match(d.name):
            tool = load(d.name)
            if tool is not None:
                out.append(tool)
    return out


def versions(name: str) -> list[int]:
    hist = _tool_dir(name) / "history"
    if not hist.is_dir():
        return []
    return sorted(int(p.name[1:]) for p in hist.iterdir()
                  if p.is_dir() and re.fullmatch(r"v\d+", p.name))


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def install(manifest: ToolManifest, code: str) -> InstalledTool:
    """Install (or replace) a tool; the previous version goes to history/."""
    d = _tool_dir(manifest.name)
    prev = load(manifest.name)
    d.mkdir(parents=True, exist_ok=True)
    version = 1
    if prev is not None:
        # after a rollback, history can hold a higher number than the current one
        version = max([prev.manifest.version, *versions(manifest.name)]) + 1
        backup = d / "history" / f"v{prev.manifest.version}"
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(prev.code_path, backup / "tool.py")
        shutil.copy2(d / "manifest.json", backup / "manifest.json")
        for old in versions(manifest.name)[:-KEEP_HISTORY]:
            shutil.rmtree(d / "history" / f"v{old}", ignore_errors=True)
    elif (d / "manifest.json").exists():
        # unreadable or tampered: keep a copy aside rather than silently replace it
        aside = d / "history" / f"broken-{int(time.time())}"
        aside.mkdir(parents=True, exist_ok=True)
        for f in ("tool.py", "manifest.json"):
            if (d / f).exists():
                shutil.copy2(d / f, aside / f)
    manifest.version = version
    manifest.code_sha256 = sha256(code)
    manifest.created_at = time.time()
    _write_atomic(d / "tool.py", code)
    _write_atomic(d / "manifest.json", manifest.to_json())
    return InstalledTool(manifest, d)


def remove(name: str) -> bool:
    try:
        d = _tool_dir(name)
    except ValueError:
        return False
    if not d.is_dir():
        return False
    trash = tools_root() / ".removed"
    trash.mkdir(parents=True, exist_ok=True)
    shutil.move(str(d), str(trash / f"{name}-{int(time.time() * 1000)}"))
    return True


def rollback(name: str) -> InstalledTool | None:
    """Make the newest saved version current again (the current one is kept in history)."""
    try:
        d = _tool_dir(name)
    except ValueError:
        return None
    saved = versions(name)
    current = load(name)
    if not saved or current is None:
        return None
    target = d / "history" / f"v{saved[-1]}"
    restored = _read(target)
    if restored is None:
        return None
    keep = d / "history" / f"v{current.manifest.version}"
    if not keep.exists():
        keep.mkdir(parents=True)
        shutil.copy2(current.code_path, keep / "tool.py")
        shutil.copy2(d / "manifest.json", keep / "manifest.json")
    shutil.copy2(target / "tool.py", d / "tool.py")
    shutil.copy2(target / "manifest.json", d / "manifest.json")
    shutil.rmtree(target, ignore_errors=True)
    return load(name)
