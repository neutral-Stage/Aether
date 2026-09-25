"""Where Aether keeps its state on disk.

Dependency-free so the audit log, memory stores and sidecar can use it without
importing the config stack.

- ``AETHER_DATA_DIR`` wins when set.
- A bundled app (the launcher sets ``AETHER_BUNDLED=1``) writes to
  ``~/Library/Application Support/Aether`` — never inside the signed bundle.
- A dev checkout keeps using ``<repo>/data``.

Config paths such as ``data/memory.db`` are resolved against the data dir, not
the process's working directory (the bundled sidecar runs from inside the app).
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    env = os.getenv("AETHER_DATA_DIR")
    if env:
        return Path(env).expanduser()
    if os.getenv("AETHER_BUNDLED") == "1":
        return Path.home() / "Library" / "Application Support" / "Aether"
    return ROOT / "data"


def resolve_data_path(path: str | os.PathLike[str] | None, default_name: str) -> Path:
    """Resolve a configured state path against :func:`data_dir`.

    Absolute paths (and ``~``) are honoured as given. A relative path is placed
    under the data dir; a leading ``data/`` component (how config.yaml spells
    these paths) is dropped so ``data/memory.db`` → ``<data_dir>/memory.db``.
    """
    if path is None or str(path).strip() == "":
        return data_dir() / default_name
    p = Path(str(path)).expanduser()
    if p.is_absolute():
        return p
    parts = p.parts
    if parts and parts[0] == "data":
        p = Path(*parts[1:]) if len(parts) > 1 else Path(default_name)
    return data_dir() / p
