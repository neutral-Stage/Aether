"""File read / write / list for the agent.

Path scope (approved_file_roots) is enforced by the policy gate before these
run; this module only does the I/O, with size caps so a huge or binary file
cannot flood the model's context.
"""
from __future__ import annotations

import fnmatch
import os
import time
from pathlib import Path

MAX_READ_BYTES = 200_000
MAX_WRITE_BYTES = 2_000_000
MAX_LIST = 200


def _resolve(path: str) -> Path:
    if not str(path or "").strip():
        raise ValueError("path is required")
    return Path(os.path.expanduser(str(path))).resolve()


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def read_file(path: str, max_bytes: int = MAX_READ_BYTES, offset: int = 0) -> str:
    """Text of a file (UTF-8, lossy), capped; binary files are summarized."""
    p = _resolve(path)
    if not p.exists():
        raise FileNotFoundError(f"no such file: {p}")
    if p.is_dir():
        raise IsADirectoryError(f"{p} is a directory — use list_dir")
    size = p.stat().st_size
    max_bytes = max(1, min(int(max_bytes or MAX_READ_BYTES), MAX_READ_BYTES))
    with p.open("rb") as fh:
        fh.seek(max(0, int(offset or 0)))
        data = fh.read(max_bytes)
    if b"\x00" in data[:4096]:
        return f"{p} is a binary file ({_human(size)}); not shown."
    text = data.decode("utf-8", errors="replace")
    end = int(offset or 0) + len(data)
    more = (f"\n…(showing bytes {int(offset or 0)}–{end} of {size}; "
            f"pass offset={end} for more)") if end < size else ""
    return f"{p} ({_human(size)}):\n{text}{more}"


def write_file(path: str, content: str, mode: str = "overwrite") -> str:
    """Write text. mode: overwrite | append | create (fails if the file exists)."""
    p = _resolve(path)
    data = str(content or "")
    if len(data.encode("utf-8")) > MAX_WRITE_BYTES:
        raise ValueError(f"content too large (> {_human(MAX_WRITE_BYTES)})")
    if mode not in ("overwrite", "append", "create"):
        raise ValueError("mode must be overwrite, append or create")
    if mode == "create" and p.exists():
        raise FileExistsError(f"{p} already exists")
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.exists()
    with p.open("a" if mode == "append" else "w", encoding="utf-8") as fh:
        fh.write(data)
    verb = "Appended to" if mode == "append" else ("Overwrote" if existed else "Created")
    return f"{verb} {p} ({len(data)} chars)."


def list_dir(path: str, pattern: str | None = None, limit: int = MAX_LIST,
             show_hidden: bool = False) -> str:
    """Directory listing: type, size, modified time; newest first."""
    p = _resolve(path)
    if not p.is_dir():
        raise NotADirectoryError(f"{p} is not a directory")
    rows = []
    for child in p.iterdir():
        if not show_hidden and child.name.startswith("."):
            continue
        if pattern and not fnmatch.fnmatch(child.name, pattern):
            continue
        try:
            st = child.stat()
        except OSError:
            continue
        rows.append((st.st_mtime, child.is_dir(), child.name, st.st_size))
    rows.sort(key=lambda r: r[0], reverse=True)
    limit = max(1, min(int(limit or MAX_LIST), MAX_LIST))
    lines = [f"{p} — {len(rows)} entries" + (f" matching {pattern!r}" if pattern else "")]
    for mtime, is_dir, name, size in rows[:limit]:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
        lines.append(f"  {'dir ' if is_dir else 'file'}  {when}  "
                     f"{'' if is_dir else _human(size):>9}  {name}{'/' if is_dir else ''}")
    if len(rows) > limit:
        lines.append(f"  …({len(rows) - limit} more)")
    return "\n".join(lines)
