"""aether doctor preflight (Phase 12)."""
from __future__ import annotations

from aether.core import doctor
from aether.core.doctor import FAIL, OK, WARN, Check, format_report, run_checks, verdict


def test_run_checks_structure():
    checks = run_checks()
    assert checks, "doctor produced no checks"
    for c in checks:
        assert isinstance(c, Check)
        assert c.status in (OK, WARN, FAIL)
        assert c.name


def test_a_broken_check_does_not_abort(monkeypatch):
    def boom() -> Check:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(doctor, "CHECKS", [doctor.check_python, boom])
    checks = run_checks()
    assert len(checks) == 2
    assert checks[1].status == WARN and "kaboom" in checks[1].detail


def test_verdict_severity():
    assert verdict([Check("a", OK)]) == OK
    assert verdict([Check("a", OK), Check("b", WARN)]) == WARN
    assert verdict([Check("a", WARN), Check("b", FAIL)]) == FAIL  # fail dominates


def test_report_shows_fixes_for_non_ok():
    checks = [Check("keys", FAIL, "none", "set ANTHROPIC_API_KEY"),
              Check("git", OK, "present")]
    report = format_report(checks)
    assert "set ANTHROPIC_API_KEY" in report
    assert "Not ready" in report          # FAIL → not ready verdict


def test_llm_backend_ok_when_key_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    c = doctor.check_llm_backend()
    assert c.status == OK and "ANTHROPIC_API_KEY" in c.detail


def test_llm_backend_fail_when_nothing(monkeypatch):
    for k in doctor._PROVIDER_ENVS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(doctor, "_ollama_up", lambda *a, **k: False)
    assert doctor.check_llm_backend().status == FAIL


def test_main_exit_code(monkeypatch):
    monkeypatch.setattr(doctor, "CHECKS", [lambda: Check("x", OK)])
    assert doctor.main() == 0
    monkeypatch.setattr(doctor, "CHECKS", [lambda: Check("x", FAIL, "bad", "fix it")])
    assert doctor.main() == 1
