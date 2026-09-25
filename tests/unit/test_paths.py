"""State paths resolve against the data dir, never the process's cwd."""
from __future__ import annotations

from pathlib import Path

import pytest

from aether.core import paths


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AETHER_DATA_DIR", raising=False)
    monkeypatch.delenv("AETHER_BUNDLED", raising=False)


def test_dev_checkout_uses_repo_data() -> None:
    assert paths.data_dir() == paths.ROOT / "data"


def test_bundled_app_uses_application_support(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHER_BUNDLED", "1")
    assert paths.data_dir() == Path.home() / "Library" / "Application Support" / "Aether"


def test_env_override_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AETHER_BUNDLED", "1")
    monkeypatch.setenv("AETHER_DATA_DIR", str(tmp_path))
    assert paths.data_dir() == tmp_path


@pytest.mark.parametrize(("configured", "expected"), [
    (None, "memory.db"),
    ("", "memory.db"),
    ("data/memory.db", "memory.db"),
    ("memory.db", "memory.db"),
    ("sub/memory.db", "sub/memory.db"),
    ("data", "memory.db"),
])
def test_relative_paths_land_in_data_dir(monkeypatch, tmp_path, configured, expected) -> None:  # noqa: ANN001
    monkeypatch.setenv("AETHER_DATA_DIR", str(tmp_path))
    assert paths.resolve_data_path(configured, "memory.db") == tmp_path / expected


def test_absolute_paths_are_kept(tmp_path: Path) -> None:
    target = tmp_path / "x" / "audit.jsonl"
    assert paths.resolve_data_path(str(target), "audit.jsonl") == target


def test_stores_follow_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AETHER_DATA_DIR", str(tmp_path))
    from aether.core.audit_log import AuditLog
    from aether.memory.skills import SkillStore
    from aether.memory.store import MemoryStore

    assert MemoryStore("data/memory.db").db_path == tmp_path / "memory.db"
    assert SkillStore("data/skills.db").db_path == tmp_path / "skills.db"
    log = AuditLog("data/audit.jsonl", hmac_key=b"k" * 32)
    assert log.path == tmp_path / "audit.jsonl"
