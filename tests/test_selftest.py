"""Self-test runner: the shipped scenarios, regressions after a policy change, and the auto-run hook."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

from controllayer import selftest
from controllayer.decision import HeuristicBackend
from controllayer.selftest import load_scenarios

from .conftest import ROOT, edit_policy

pytestmark = [pytest.mark.control("selftest")]


@pytest.fixture
def tests_client(make_client, monkeypatch):
    monkeypatch.setenv("ACL_SCENARIOS", str(ROOT / "scenarios.yaml"))
    # The offline heuristic: the same verdicts on every machine, whether or not the ONNX classifier is installed.
    c = make_client(backend=HeuristicBackend())
    c.app.state.selftest.auto = True
    return c


def _wait(pred, timeout: float = 10.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def test_shipped_scenarios_load_and_cover_the_controls():
    scenarios = load_scenarios(ROOT / "scenarios.yaml")
    assert len(scenarios) >= 10
    ids = [sc["id"] for sc in scenarios]
    assert len(ids) == len(set(ids))
    controls = {st.get("control") for sc in scenarios for st in sc["steps"]}
    assert {"secrets", "pii", "signatures", "prompt_injection", "tool_access", "model_allowlist"} <= controls


def test_run_passes_on_the_shipped_policy(tests_client):
    r = tests_client.post("/admin/selftest/run")
    assert r.status_code == 200
    res = r.json()
    assert res["error"] is None
    assert res["trigger"] == "manual"
    assert res["semantic_backend"] == "HeuristicBackend"
    s = res["summary"]
    assert s["failed"] == 0, [
        (sc["id"], st["text"], st["actual"])
        for sc in res["scenarios"]
        for st in sc["steps"]
        if not st["passed"] and not st["known_gap"]
    ]
    assert s["steps"] == s["passed"] + s["known_gaps"]
    step = res["scenarios"][0]["steps"][0]
    assert {"expect", "actual", "passed", "latency_ms", "controls"} <= set(step)
    latest = tests_client.get("/admin/selftest/latest").json()
    assert latest["result"]["run_id"] == res["run_id"]
    assert latest["status"]["policy_version"] == res["policy_version"]


def test_a_run_spends_no_budget_and_opens_no_incident(tests_client):
    app = tests_client.app
    tests_client.post("/admin/selftest/run")
    assert all(p.get("score", 0) == 0 for p in tests_client.get("/admin/risk").json()["principals"])
    assert app.state.layer.usage.leases(open_only=False, limit=10) == []
    assert tests_client.get("/admin/incidents").json().get("incidents", []) == []


def test_policy_change_reports_regressions(tests_client, policy_dir: Path):
    app = tests_client.app
    first = tests_client.post("/admin/selftest/run").json()
    edit_policy(policy_dir, lambda d: d["secrets"].update(mode="log"))  # weaken: secrets only logged
    app.state.selftest.auto = False
    app.state.store.reload()
    second = tests_client.post("/admin/selftest/run").json()
    assert second["policy_version"] != first["policy_version"]
    assert second["prev_version"] == first["policy_version"]
    assert second["summary"]["regressions"] >= 1
    assert {r["scenario"] for r in second["regressions"]} >= {"secret"}
    assert second["note"].startswith(f"policy {first['policy_version']} -> {second['policy_version']}: ")
    hist = tests_client.get("/admin/selftest/history").json()["runs"]
    assert [h["run_id"] for h in hist] == [second["run_id"], first["run_id"]]


def test_auto_run_after_a_policy_reload_is_debounced(tests_client, policy_dir: Path):
    app = tests_client.app
    st = app.state.selftest
    st.debounce = 60  # the timer never fires by itself here; the test fires it, so load cannot race it
    before = app.state.store.policy.version
    edit_policy(policy_dir, lambda d: d["models"]["allowed"].append("mistral:*"))
    app.state.store.reload()
    first = st._timer
    edit_policy(policy_dir, lambda d: d["models"]["allowed"].append("phi3:*"))
    app.state.store.reload()  # a second edit inside the window restarts it: one run, not two
    assert first is not st._timer and first.finished.is_set()  # cancelled
    assert st.pending == {"from": before, "since": pytest.approx(time.time(), abs=5)}
    assert tests_client.get("/admin/selftest/latest").json()["status"]["pending"]["from"] == before
    st._timer.cancel()
    st._fire()
    assert st.pending is None and len(st.history) == 1
    res = st.latest()
    assert res["trigger"] == "auto"
    assert res["prev_version"] == before
    assert res["policy_version"] == app.state.store.policy.version
    assert res["note"] == f"policy {before} -> {res['policy_version']}: 0 regressions"


def test_auto_run_follows_admin_overlay_changes(tests_client):
    app = tests_client.app
    st = app.state.selftest
    st.debounce = 0.05
    before = app.state.store.policy.version
    app.state.store.write_overlay({"principals": {"carol": {"status": "revoked", "reason": "left"}}})
    _wait(lambda: st.latest() is not None)
    res = st.latest()
    assert res["prev_version"] == before
    # carol's steps now fail with auth instead of their expected outcome
    assert (
        any(r["scenario"] in ("injection", "permissions", "safe") for r in res["regressions"])
        or res["summary"]["failed"] > 0
    )


def test_auto_run_can_be_switched_off(tests_client, policy_dir: Path, monkeypatch):
    st = tests_client.app.state.selftest
    st.auto = False
    edit_policy(policy_dir, lambda d: d["models"]["allowed"].append("mistral:*"))
    tests_client.app.state.store.reload()
    assert st.pending is None


def test_bad_scenarios_file_is_reported_not_raised(make_client, tmp_path, monkeypatch):
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"scenarios": [{"id": "x", "steps": [{"direction": "sideways"}]}]}))
    monkeypatch.setenv("ACL_SCENARIOS", str(bad))
    res = make_client(backend=HeuristicBackend()).post("/admin/selftest/run").json()
    assert res["error"].startswith("scenarios not loaded")
    assert res["scenarios"] == []


def test_expected_control_must_be_among_the_findings(make_client, tmp_path, monkeypatch):
    f = tmp_path / "s.yaml"
    step = {
        "principal": "alice",
        "direction": "input",
        "model": "gpt-4o",
        "expect": ["block"],
        "text": "aws_access_key_id = AKIAIOSFODNN7EXAMPLE",
    }
    f.write_text(
        yaml.safe_dump(
            {
                "scenarios": [
                    {"id": "right", "steps": [{**step, "control": "secrets"}]},
                    {"id": "wrong", "steps": [{**step, "control": "pii"}]},
                ]
            }
        )
    )
    monkeypatch.setenv("ACL_SCENARIOS", str(f))
    res = make_client(backend=HeuristicBackend()).post("/admin/selftest/run").json()
    assert [sc["passed"] for sc in res["scenarios"]] == [True, False]


def test_pytest_report_endpoint(make_client, tmp_path, monkeypatch):
    report = tmp_path / "reports" / "acl-report.json"
    monkeypatch.setenv("ACL_PYTEST_REPORT", str(report))
    c = make_client(backend=HeuristicBackend())
    assert c.get("/admin/selftest/pytest").json() == {"report": None, "path": str(report), "junit": False}
    assert c.get("/admin/selftest/junit.xml").status_code == 404
    report.parent.mkdir()
    report.write_text('{"summary": {"total": 1}, "tests": []}')
    (report.parent / "junit.xml").write_text("<testsuites/>")
    got = c.get("/admin/selftest/pytest").json()
    assert got["report"]["summary"]["total"] == 1 and got["junit"] is True
    assert c.get("/admin/selftest/junit.xml").text == "<testsuites/>"


def test_history_survives_a_restart(make_client, monkeypatch):
    monkeypatch.setenv("ACL_SCENARIOS", str(ROOT / "scenarios.yaml"))
    run = make_client(backend=HeuristicBackend()).post("/admin/selftest/run").json()
    again = make_client(backend=HeuristicBackend())
    assert again.get("/admin/selftest/latest").json()["result"]["run_id"] == run["run_id"]
    assert selftest.HISTORY == 20


def test_startup_runs_when_the_policy_changed_while_down(make_client, monkeypatch):
    monkeypatch.setenv("ACL_SCENARIOS", str(ROOT / "scenarios.yaml"))
    monkeypatch.setenv("ACL_SELFTEST_AUTO", "1")
    with make_client(backend=HeuristicBackend()) as c:
        _wait(lambda: c.app.state.selftest.latest() is not None)
        assert c.app.state.selftest.latest()["trigger"] == "startup"
    with make_client(backend=HeuristicBackend()) as c:  # same policy, history on disk: nothing to do
        assert not c.app.state.selftest.stale()
        assert len(c.app.state.selftest.history) == 1
