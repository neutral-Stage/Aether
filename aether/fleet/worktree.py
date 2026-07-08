"""Git worktree isolation for fleet agents."""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(  # noqa: S603
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        # e.g. cwd doesn't exist → behave like a failed git invocation
        return subprocess.CompletedProcess(args, returncode=128, stdout="", stderr=str(e))


def provision(workspace: Path, session_id: str) -> tuple[Path, str | None, str | None]:
    """Create an isolated worktree. Returns (cwd, branch, warning).

    Fallback ladder: worktree → in-place with warning (caller may refuse via
    fleet.require_isolation).
    """
    top = _git(["rev-parse", "--show-toplevel"], workspace)
    if top.returncode != 0:
        return workspace, None, f"not a git repo — running in place: {workspace}"
    root = Path(top.stdout.strip())
    branch = f"aether/{session_id}"
    wt_dir = root / ".aether-worktrees" / session_id
    res = _git(["worktree", "add", "-b", branch, str(wt_dir)], root)
    if res.returncode != 0:
        err = (res.stderr or res.stdout).strip()[:200]
        return workspace, None, f"worktree failed ({err}) — running in place"
    return wt_dir, branch, None


def cleanup(workspace: Path, worktree_dir: str | None, *, keep: bool = False) -> None:
    """Remove the worktree dir (branch is kept for the user to inspect/merge)."""
    if keep or not worktree_dir:
        return
    top = _git(["rev-parse", "--show-toplevel"], Path(workspace))
    if top.returncode != 0:
        return
    _git(["worktree", "remove", "--force", worktree_dir], Path(top.stdout.strip()))


def repo_root(workspace: Path | str) -> Path | None:
    top = _git(["rev-parse", "--show-toplevel"], Path(workspace))
    return Path(top.stdout.strip()) if top.returncode == 0 else None


def current_ref(root: Path | str) -> str:
    """Best commit-ish to use as the integration base (branch name or HEAD)."""
    res = _git(["symbolic-ref", "--short", "-q", "HEAD"], Path(root))
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    return "HEAD"


def commit_all(worktree_dir: Path | str, message: str) -> bool:
    """Stage + commit everything in a node's worktree so its branch carries the
    diff. Returns False when there was nothing to commit."""
    d = Path(worktree_dir)
    _git(["add", "-A"], d)
    res = _git(["commit", "-m", message], d)
    return res.returncode == 0


def merge_tree_conflicts(root: Path | str, base_ref: str, branch: str) -> tuple[bool, list[str]]:
    """Pre-flight: would merging `branch` onto `base_ref` conflict?

    Returns (has_conflicts, conflicted_paths). Uses `git merge-tree
    --write-tree` (git ≥2.38): exit 0 = clean, 1 = conflicts. No working tree is
    touched — this is a dry run.
    """
    res = _git(
        ["merge-tree", "--write-tree", "--name-only", base_ref, branch], Path(root)
    )
    if res.returncode == 0:
        return False, []
    if res.returncode != 1:  # actual error (bad ref etc.) — be conservative
        return True, [(res.stderr or res.stdout).strip()[:200]]
    # stdout: <tree-oid>\n<conflicted path>\n...  (best-effort parse)
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    return True, lines[1:] if len(lines) > 1 else []
