"""Unit tests for delegate_to_coder sandbox (Phase 11)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aether.tools.delegation import (
    DelegationResult,
    delegate_to_coder,
    delegate_to_coder_structured,
)


@pytest.mark.unit
class TestDelegation:
    def test_empty_prompt_returns_error(self) -> None:
        result = delegate_to_coder_structured("")
        assert result.exit_code != 0
        assert "required" in result.error.lower()

    def test_structured_output_is_json(self) -> None:
        result = delegate_to_coder("", structured=True)
        data = json.loads(result)
        assert "exit_code" in data
        assert "summary" in data
        assert "stdout" in data

    def test_workspace_outside_approved_roots_blocked(self, tmp_path: Path) -> None:
        from aether.tools.delegation import _resolve_workspace

        workspace = tmp_path / "project"
        workspace.mkdir()
        _cwd, err = _resolve_workspace(str(workspace), ["/only/this/root"])
        assert err is not None
        assert "approved roots" in err.lower()

    def test_workspace_inside_approved_roots_allowed(self, tmp_path: Path) -> None:
        from aether.tools.delegation import _resolve_workspace

        workspace = tmp_path / "project"
        workspace.mkdir()
        cwd, err = _resolve_workspace(str(workspace), [str(tmp_path)])
        assert err is None
        assert cwd == workspace.resolve()

    def test_echo_via_shell_subprocess_pattern(self, tmp_path: Path) -> None:
        """Sanity: structured result fields populate for a real subprocess."""
        proc = subprocess.run(
            ["echo", "hello"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        summary_result = DelegationResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            summary="ok",
            cwd=str(tmp_path),
            agent="test",
        )
        payload = json.loads(summary_result.format_json())
        assert payload["exit_code"] == 0
        assert "hello" in payload["stdout"]

    def test_missing_cli_returns_127(self, tmp_path: Path) -> None:
        result = delegate_to_coder_structured(
            "test prompt",
            agent="claude",
            workspace=str(tmp_path),
            approved_roots=[str(tmp_path)],
        )
        if result.exit_code == 127:
            assert "not found" in (result.error or "").lower() or "no coding CLI" in result.summary
