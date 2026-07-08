"""Task DAG: ready-set, path conflicts, cycles, skip propagation (Phase 8)."""
from __future__ import annotations

import pytest

from aether.fleet.graph import TaskGraph, TaskNode, _paths_conflict


def _n(nid, deps=None, paths=None):
    return TaskNode(id=nid, title=nid, prompt=f"do {nid}",
                    depends_on=deps or [], paths=paths or [])


def test_paths_conflict_rules():
    assert _paths_conflict(["src/api/*"], ["src/api/db.py"])         # glob vs file in it
    assert _paths_conflict([], ["src/x.py"])                         # unknown scope
    assert _paths_conflict(["*.py"], ["src/x.py"])                   # bare glob = repo-wide
    assert _paths_conflict(["src/x"], ["src/x/y.py"])               # nested under a dir scope
    assert _paths_conflict(["src/api"], ["src/api"])                # same scope
    # two DIFFERENT files (even in the same dir) do not merge-conflict
    assert not _paths_conflict(["src/api/x.py"], ["src/api/y.py"])
    assert not _paths_conflict(["src/api/x.py"], ["src/ui/y.py"])
    assert not _paths_conflict(["docs/a.md"], ["src/b.py"])


def test_validate_rejects_unknown_dep():
    g = TaskGraph("g1", "goal", [_n("a", deps=["ghost"])])
    with pytest.raises(ValueError, match="unknown node"):
        g.validate()


def test_validate_rejects_cycle():
    g = TaskGraph("g1", "goal", [_n("a", deps=["b"]), _n("b", deps=["a"])])
    with pytest.raises(ValueError, match="cycle"):
        g.validate()


def test_ready_respects_dependencies():
    g = TaskGraph("g", "goal", [_n("a", paths=["x/1"]), _n("b", deps=["a"], paths=["y/1"])])
    g.validate()
    ready = [n.id for n in g.ready([])]
    assert ready == ["a"]           # b blocked on a
    g.nodes["a"].status = "done"
    assert [n.id for n in g.ready([])] == ["b"]


def test_ready_serializes_conflicting_nodes():
    # two independent nodes touching the same dir must not run together
    g = TaskGraph("g", "goal", [_n("a", paths=["src/api/*"]), _n("b", paths=["src/api/db.py"])])
    g.validate()
    ready = g.ready([])
    assert len(ready) == 1          # only one picked; the other serializes

    g2 = TaskGraph("g", "goal", [_n("a", paths=["src/api/x"]), _n("b", paths=["src/ui/y"])])
    g2.validate()
    assert len(g2.ready([])) == 2   # disjoint → both parallel


def test_ready_excludes_conflict_with_running():
    g = TaskGraph("g", "goal", [_n("a", paths=["src/x"]), _n("b", paths=["src/x/y"])])
    g.validate()
    g.nodes["a"].status = "running"
    assert g.ready(["a"]) == []     # b conflicts with running a


def test_skip_propagation():
    g = TaskGraph("g", "goal", [
        _n("a", paths=["x"]), _n("b", deps=["a"], paths=["y"]), _n("c", deps=["b"], paths=["z"]),
    ])
    g.validate()
    g.nodes["a"].status = "failed"
    g.propagate_skips()
    assert g.nodes["b"].status == "skipped"
    assert g.nodes["c"].status == "skipped"   # transitive
    assert g.is_complete()


def test_paths_conflict_normalizes_spellings():
    # same file, cosmetic spelling differences → must conflict (else parallel = merge collision)
    assert _paths_conflict(["./src/x.py"], ["src/x.py"])
    assert _paths_conflict(["src/x.py"], ["src/a/../x.py"])
    assert _paths_conflict(["src/x/"], ["src/x"])
    assert _paths_conflict(["src/./api"], ["src/api/db.py"])   # nested after normalize
    assert _paths_conflict(["."], ["src/x.py"])                # normalizes to repo-root


def test_done_in_order():
    g = TaskGraph("g", "goal", [_n("a", paths=["x"]), _n("b", deps=["a"], paths=["y"])])
    for n in g.nodes.values():
        n.status = "done"
        n.branch = f"aether/{n.id}"
    ordered = [n.id for n in g.done_in_order()]
    assert ordered.index("a") < ordered.index("b")
