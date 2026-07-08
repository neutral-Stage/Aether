"""Task DAG — the orchestration layer over the flat fleet (Phase 8).

A goal decomposes into TaskNodes joined by depends_on edges. The runner schedules
ready, non-conflicting nodes onto fleet sessions, gates each code change, and
synthesizes one result. This is the coordination the flat SessionManager lacks:
dependencies, parallel-only-where-safe, and a single combined outcome.

Pure data + scheduling logic — no subprocess/git here, so it unit-tests cleanly.
"""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from typing import Any

NODE_STATES = ("pending", "running", "done", "failed", "skipped")
_TERMINAL_NODE = ("done", "failed", "skipped")


def _norm(path: str) -> str:
    """Normalize a declared path so cosmetic spellings compare equal:
    './src/x' , 'src/x/' , 'src/a/../x' all collapse to 'src/x'. Globs survive
    (normpath keeps '*'), so 'src/api/*' stays a directory scope."""
    p = os.path.normpath(path.strip())
    return "" if p == "." else p


def _scope_base(pattern: str) -> str:
    """Non-glob prefix of a path pattern, e.g. 'src/api/*.py' -> 'src/api'."""
    return pattern.split("*")[0].rstrip("/")


def _pair_conflict(pa: str, pb: str) -> bool:
    if pa == pb:
        return True
    ag, bg = "*" in pa, "*" in pb
    if ag and not bg:
        return fnmatch.fnmatch(pb, pa)          # does file pb fall in glob pa?
    if bg and not ag:
        return fnmatch.fnmatch(pa, pb)
    if ag and bg:                               # both globs → overlapping dir scope
        ba, bb = _scope_base(pa), _scope_base(pb)
        return (not ba or not bb or ba == bb
                or ba.startswith(bb + "/") or bb.startswith(ba + "/"))
    # both concrete → conflict only if one path is nested under the other
    return pa.startswith(pb + "/") or pb.startswith(pa + "/")


def _paths_conflict(a: list[str], b: list[str]) -> bool:
    """True if two nodes' declared path scopes might touch the same file.

    Unknown scope (empty list) is treated as repo-wide → conflicts with
    everything. The bias is toward serializing: a false "conflict" only costs
    parallelism, a false "safe" costs a merge collision.
    """
    if not a or not b:
        return True
    na = [_norm(p) for p in a]
    nb = [_norm(p) for p in b]
    if "" in na or "" in nb:  # a path that normalized to repo-root
        return True
    return any(_pair_conflict(pa, pb) for pa in na for pb in nb)


@dataclass
class TaskNode:
    id: str
    title: str
    prompt: str
    agent_type: str = "claude"
    depends_on: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)  # file globs this node will touch
    gate_cmd: str | None = None                     # deterministic check in the worktree
    difficulty: str = "medium"                      # low | medium | high (tier routing)
    status: str = "pending"
    session_id: str | None = None
    branch: str | None = None
    result: str = ""
    error: str = ""

    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_NODE

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "agent_type": self.agent_type,
            "depends_on": list(self.depends_on), "paths": list(self.paths),
            "difficulty": self.difficulty, "status": self.status,
            "session_id": self.session_id, "branch": self.branch,
            "result": self.result[:500], "error": self.error[:300],
        }


class TaskGraph:
    """A DAG of TaskNodes. Not thread-safe on its own; the runner owns mutation."""

    def __init__(self, graph_id: str, goal: str, nodes: list[TaskNode]) -> None:
        self.graph_id = graph_id
        self.goal = goal
        self.nodes: dict[str, TaskNode] = {n.id: n for n in nodes}
        self.integration_branch: str | None = None
        self.result: str = ""
        self.status: str = "pending"  # pending | running | done | failed | interrupted
        self.cancelled = False        # set by STOP; the runner strands + finishes
        if len(self.nodes) != len(nodes):
            raise ValueError("duplicate node ids")

    def validate(self) -> None:
        for n in self.nodes.values():
            for dep in n.depends_on:
                if dep not in self.nodes:
                    raise ValueError(f"node {n.id!r} depends on unknown node {dep!r}")
        self._check_acyclic()

    def _check_acyclic(self) -> None:
        WHITE, GREY, BLACK = 0, 1, 2
        color = {nid: WHITE for nid in self.nodes}

        def visit(nid: str, stack: list[str]) -> None:
            color[nid] = GREY
            for dep in self.nodes[nid].depends_on:
                if color[dep] == GREY:
                    cycle = " → ".join(stack + [nid, dep])
                    raise ValueError(f"dependency cycle: {cycle}")
                if color[dep] == WHITE:
                    visit(dep, stack + [nid])
            color[nid] = BLACK

        for nid in self.nodes:
            if color[nid] == WHITE:
                visit(nid, [])

    def propagate_skips(self) -> None:
        """A pending node whose dependency failed/was skipped can never run."""
        changed = True
        while changed:
            changed = False
            for n in self.nodes.values():
                if n.status != "pending":
                    continue
                deps = [self.nodes[d] for d in n.depends_on]
                if any(d.status in ("failed", "skipped") for d in deps):
                    n.status = "skipped"
                    n.error = "dependency failed"
                    changed = True

    def ready(self, running_ids: list[str]) -> list[TaskNode]:
        """Pending nodes whose deps are all done and that path-conflict with
        neither a running node nor an already-picked ready node."""
        running = [self.nodes[i] for i in running_ids if i in self.nodes]
        candidates = []
        for n in self.nodes.values():
            if n.status != "pending":
                continue
            deps = [self.nodes[d] for d in n.depends_on]
            if not all(d.status == "done" for d in deps):
                continue
            if any(_paths_conflict(n.paths, r.paths) for r in running):
                continue
            candidates.append(n)
        picked: list[TaskNode] = []
        for n in candidates:
            if any(_paths_conflict(n.paths, p.paths) for p in picked):
                continue  # serialize colliding ready nodes
            picked.append(n)
        return picked

    def running(self) -> list[TaskNode]:
        return [n for n in self.nodes.values() if n.status == "running"]

    def done_in_order(self) -> list[TaskNode]:
        """Done nodes in a dependency-respecting order (for integration)."""
        ordered: list[TaskNode] = []
        seen: set[str] = set()

        def emit(nid: str) -> None:
            if nid in seen:
                return
            seen.add(nid)
            for dep in self.nodes[nid].depends_on:
                emit(dep)
            n = self.nodes[nid]
            if n.status == "done":
                ordered.append(n)

        for nid in self.nodes:
            emit(nid)
        return ordered

    def is_complete(self) -> bool:
        return all(n.is_terminal() for n in self.nodes.values())

    def has_failures(self) -> bool:
        return any(n.status in ("failed", "skipped") for n in self.nodes.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "goal": self.goal,
            "status": self.status,
            "integration_branch": self.integration_branch,
            "result": self.result[:1000],
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "counts": {
                s: sum(1 for n in self.nodes.values() if n.status == s)
                for s in NODE_STATES
            },
        }
