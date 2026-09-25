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


# ---- default-brain and first-run checks (Phase A) ----

def test_brain_key_uses_the_configured_provider(monkeypatch):
    monkeypatch.setattr(doctor, "_brain_role",
                        lambda: ("zai", {"api_key_env": "ZAI_API_KEY", "model": "glm-5.3-flash"}))
    for k in doctor._PROVIDER_ENVS:  # noqa: SLF001
        monkeypatch.delenv(k, raising=False)
    assert doctor.check_brain_key().status == FAIL
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    c = doctor.check_brain_key()
    assert c.status == WARN and "failover" in c.detail
    monkeypatch.setenv("ZAI_API_KEY", "y")
    assert doctor.check_brain_key().status == OK


def test_brain_online_is_opt_in(monkeypatch):
    monkeypatch.delenv("AETHER_DOCTOR_ONLINE", raising=False)
    c = doctor.check_brain_online()
    assert c.status == OK and "skipped" in c.detail


def test_brain_online_reports_rejected_key(monkeypatch):
    import io
    import urllib.error

    monkeypatch.setenv("AETHER_DOCTOR_ONLINE", "1")
    monkeypatch.setenv("ZAI_API_KEY", "bad")
    monkeypatch.setattr(doctor, "_brain_role", lambda: ("zai", {
        "api_key_env": "ZAI_API_KEY", "model": "glm-5.3-flash", "backend": "openai_compatible",
        "base_url": "https://api.z.ai/api/coding/paas/v4"}))

    def reject(req, timeout=0):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b""))
    monkeypatch.setattr("urllib.request.urlopen", reject)
    assert doctor.check_brain_online().status == FAIL


def test_brain_online_flags_unlisted_model(monkeypatch):
    import json

    monkeypatch.setenv("AETHER_DOCTOR_ONLINE", "1")
    monkeypatch.setenv("ZAI_API_KEY", "ok")
    monkeypatch.setattr(doctor, "_brain_role", lambda: ("zai", {
        "api_key_env": "ZAI_API_KEY", "model": "glm-5.3-flash", "backend": "openai_compatible",
        "base_url": "https://example.test/v4"}))

    class Resp:
        def __init__(self, ids):  # noqa: ANN001
            self.body = json.dumps({"data": [{"id": i} for i in ids]}).encode()
        def read(self):  # noqa: ANN201
            return self.body
        def __enter__(self):  # noqa: ANN204
            return self
        def __exit__(self, *a):  # noqa: ANN002
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: Resp(["glm-5-turbo"]))
    assert doctor.check_brain_online().status == WARN
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: Resp(["glm-5.3-flash"]))
    assert doctor.check_brain_online().status == OK


def test_grounding_calibration_check(monkeypatch, tmp_path):
    monkeypatch.setenv("AETHER_DATA_DIR", str(tmp_path))
    assert doctor.check_grounding_calibrated().status == WARN
    (tmp_path / "grounding_calibration.json").write_text(
        '{"model": "glm-5.3-flash", "coord_space": "pixels", "max_image_edge": 1600, "hit_rate": 0.85}')
    c = doctor.check_grounding_calibrated()
    assert c.status == OK and "85%" in c.detail


def test_data_dir_check(monkeypatch, tmp_path):
    monkeypatch.setenv("AETHER_DATA_DIR", str(tmp_path / "state"))
    assert doctor.check_data_dir().status == OK
    assert (tmp_path / "state").is_dir()


def test_sidecar_doctor_endpoint(sidecar_client):
    body = sidecar_client.get("/doctor").json()
    assert body["verdict"] in (OK, WARN, FAIL)
    names = {c["name"] for c in body["checks"]}
    assert "Data directory writable" in names
