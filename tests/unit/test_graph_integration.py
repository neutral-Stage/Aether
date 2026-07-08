"""Git conflict pre-flight + branch synthesis with a real repo (Phase 8)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aether.fleet import synthesize
from aether.fleet import worktree as wt


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init", "-b", "main"], root)
    _git(["config", "user.email", "t@t.co"], root)
    _git(["config", "user.name", "t"], root)
    (root / "a.txt").write_text("base\n")
    (root / "b.txt").write_text("base\n")
    _git(["add", "-A"], root)
    _git(["commit", "-m", "base"], root)
    return root


def _branch_editing(root, session_id, filename, content):
    wt_dir, branch, warn = wt.provision(root, session_id)
    assert branch, warn
    (Path(wt_dir) / filename).write_text(content)
    assert wt.commit_all(wt_dir, f"edit {filename}")
    return wt_dir, branch


def test_merge_tree_clean_for_disjoint_files(repo):
    _branch_editing(repo, "s1", "a.txt", "changed A\n")
    _, branch = _branch_editing(repo, "s2", "b.txt", "changed B\n")
    has_conflict, paths = wt.merge_tree_conflicts(repo, "main", branch)
    assert has_conflict is False
    assert paths == []


def test_merge_tree_detects_conflict_same_file(repo):
    _branch_editing(repo, "s1", "a.txt", "line from one\n")
    # base 'main' still has "base\n"; a second branch also editing a.txt differently
    _, branch2 = _branch_editing(repo, "s2", "a.txt", "line from two\n")
    # merge branch2 onto branch1's tip would conflict; here vs a shared base it's clean,
    # so build the real conflict: merge s1 into main first, then check s2.
    _git(["merge", "--no-edit", "aether/s1"], repo)
    has_conflict, _ = wt.merge_tree_conflicts(repo, "main", branch2)
    assert has_conflict is True


def test_integrate_merges_disjoint_branches(repo):
    _, b1 = _branch_editing(repo, "s1", "a.txt", "A2\n")
    _, b2 = _branch_editing(repo, "s2", "b.txt", "B2\n")
    report = synthesize.integrate(repo, "main", [b1, b2], "g1")
    assert report["ok"]
    assert set(report["merged"]) == {b1, b2}
    assert report["conflicted"] == []
    # integration branch has BOTH edits
    show_a = _git(["show", f"{report['branch']}:a.txt"], repo).stdout
    show_b = _git(["show", f"{report['branch']}:b.txt"], repo).stdout
    assert "A2" in show_a and "B2" in show_b


def test_integrate_reports_conflict_and_keeps_clean_merges(repo):
    _, b1 = _branch_editing(repo, "s1", "a.txt", "from one\n")
    _, b2 = _branch_editing(repo, "s2", "a.txt", "from two\n")   # conflicts with b1
    _, b3 = _branch_editing(repo, "s3", "b.txt", "B3\n")         # clean
    report = synthesize.integrate(repo, "main", [b1, b2, b3], "g2")
    assert report["ok"]
    assert b1 in report["merged"]
    assert b2 in report["conflicted"]        # second edit to a.txt conflicts
    assert b3 in report["merged"]            # unrelated file still integrates
    summary = synthesize.summarize(
        {"counts": {"done": 3, "failed": 0, "skipped": 0}}, report)
    assert "conflicted" in summary and report["branch"] in summary


def test_integrate_no_branches(repo):
    report = synthesize.integrate(repo, "main", [], "g3")
    assert report["ok"] and report["branch"] is None
