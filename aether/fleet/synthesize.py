"""Result synthesis — merge fleet node branches into one integration branch.

Never touches the user's checked-out branch. Merges each completed node's
branch, in dependency order, into a fresh `aether/integration/<graph_id>`
branch built in a throwaway worktree. Conflicting branches are reported, not
force-resolved — the human reviews the integration branch and the conflicts.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import worktree as wt


def integrate(
    root: Path | str, base_ref: str, ordered_branches: list[str], graph_id: str,
) -> dict[str, Any]:
    """Build aether/integration/<graph_id> from base_ref, merging each branch in
    order. Returns {ok, branch, merged, conflicted, error}."""
    root = Path(root)
    branches = [b for b in ordered_branches if b]
    if not branches:
        return {"ok": True, "branch": None, "merged": [], "conflicted": [],
                "error": "no branches to integrate"}

    integ_branch = f"aether/integration/{graph_id}"
    integ_wt = root / ".aether-worktrees" / f"integration-{graph_id}"

    add = wt._git(["worktree", "add", "-b", integ_branch, str(integ_wt), base_ref], root)
    if add.returncode != 0:
        return {"ok": False, "branch": None, "merged": [], "conflicted": [],
                "error": (add.stderr or add.stdout).strip()[:300]}

    merged: list[str] = []
    conflicted: list[str] = []
    try:
        for br in branches:
            res = wt._git(["merge", "--no-edit", br], integ_wt)
            if res.returncode == 0:
                merged.append(br)
            else:
                conflicted.append(br)
                # Abort + hard-reset so a failed --abort can't wedge the index and
                # cascade false conflicts onto every later (unrelated) branch.
                wt._git(["merge", "--abort"], integ_wt)
                wt._git(["reset", "--hard"], integ_wt)
    finally:
        wt._git(["worktree", "remove", "--force", str(integ_wt)], root)

    return {"ok": True, "branch": integ_branch, "merged": merged,
            "conflicted": conflicted, "error": None}


def summarize(graph_result: dict[str, Any], integ: dict[str, Any]) -> str:
    """One-line human/agent summary of a completed graph + integration."""
    counts = graph_result.get("counts", {})
    parts = [
        f"{counts.get('done', 0)} done",
        f"{counts.get('failed', 0)} failed",
        f"{counts.get('skipped', 0)} skipped",
    ]
    line = f"Graph complete: {', '.join(parts)}."
    if integ.get("branch"):
        line += f" Integrated → {integ['branch']}"
        if integ.get("conflicted"):
            line += f" ({len(integ['conflicted'])} branch(es) conflicted, left for review)"
        line += "."
    elif integ.get("error"):
        line += f" Integration: {integ['error']}."
    return line
