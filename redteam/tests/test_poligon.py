"""Poligon: scoring, evasion mutants, the impact of a configuration change, feed self-test."""

from __future__ import annotations

from pathlib import Path

import pytest
from shield_redteam.poligon import corpus
from shield_redteam.poligon.feedcheck import check_feed
from shield_redteam.poligon.mutate import MUTATORS, mutants
from shield_redteam.poligon.runner import run
from shield_redteam.poligon.score import impact, report
from shield_redteam.poligon.service import Poligon

ROOT = Path(__file__).parent.parent


# --- scoring ---------------------------------------------------------------------


def test_report_counts_stopped_attacks_and_false_positives(layer, cases):
    rep = report(run(layer.target(), cases))
    assert rep["attacks"] == {"total": 3, "stopped": 2}
    assert rep["benign"] == {"total": 2, "stopped": 1}
    assert rep["protection"] == pytest.approx(2 / 3, abs=1e-3)
    assert rep["friction"] == 0.5
    assert [o["id"] for o in rep["open_attacks"]] == ["a3"]
    assert [o["id"] for o in rep["false_positives"]] == ["b2"]


def test_posture_punishes_blocking_everything(cases):
    from tests.conftest import FakeLayer

    block_all, allow_all = FakeLayer(["."]), FakeLayer([])
    assert report(run(block_all.target(), cases))["posture"] == 0.0
    assert report(run(allow_all.target(), cases))["posture"] == 0.0


def test_perfect_layer_scores_100(cases):
    from tests.conftest import FakeLayer

    perfect = FakeLayer(["ignore .*instructions", "AKIA", "dump the database"])
    assert report(run(perfect.target(), cases))["posture"] == 100.0


def test_breakdown_by_owasp(layer, cases):
    rows = {g["name"]: g for g in report(run(layer.target(), cases))["by_owasp"]}
    assert rows["LLM01"]["protection"] == 1.0
    assert rows["LLM02"]["stopped"] == 1 and rows["LLM02"]["attacks"] == 2


def test_target_error_is_never_counted_as_a_pass(cases):
    from shield_redteam.core.target import CallableTarget
    from shield_redteam.core.types import Decision

    down = CallableTarget(lambda c: Decision("error", error="connection refused"))
    rep = report(run(down, cases))
    assert rep["errors"] == len(cases) and rep["protection"] == 0.0


# --- evasion ---------------------------------------------------------------------


def test_mutants_only_for_mutable_attacks(cases):
    ms = mutants(cases)
    assert {m.parent for m in ms} == {"a1", "a3"}  # a2 is immutable, b* are benign
    assert all(m.must_stop and m.technique in MUTATORS for m in ms)


def test_evasion_counts_only_mutants_of_stopped_attacks(layer, cases):
    rep = report(run(layer.target(), cases + mutants(cases)))
    # a3 is not stopped in the first place, so its mutants say nothing about evasion.
    assert rep["mutants"]["total"] == len(MUTATORS)
    by = {t["name"]: t for t in rep["by_technique"]}
    assert by["upper"]["stopped"] == 1  # the fake layer is case-insensitive
    assert by["leet"]["stopped"] == 0  # "1gn0r3" walks past the regex
    assert 0 < rep["evasion"] < 1


# --- impact of a configuration change -----------------------------------------------


def test_impact_names_attacks_opened_by_removing_a_control(layer, cases):
    before = run(layer.target(), cases)
    layer.rules.remove("ignore .*instructions")
    after = run(layer.target(), cases)
    delta = impact(before, after)
    assert [o["id"] for o in delta["opened"]] == ["a1"]
    assert delta["opened"][0]["was"] == "block" and delta["opened"][0]["action"] == "allow"
    assert delta["opened_by_category"] == {"prompt_injection": 1}
    assert delta["posture_after"] < delta["posture_before"]


def test_impact_reports_closed_attacks_and_fixed_false_positives(layer, cases):
    before = run(layer.target(), cases)
    layer.rules.remove("ransomware")
    layer.rules.append("dump the database")
    delta = impact(before, run(layer.target(), cases))
    assert [o["id"] for o in delta["closed"]] == ["a3"]
    assert [o["id"] for o in delta["fixed_false_positives"]] == ["b2"]
    assert not delta["opened"] and not delta["new_false_positives"]


def test_service_reruns_on_config_change_and_logs_it(layer, cases):
    p = Poligon(layer.target(), cases, mutate=False, interval=3600)
    assert p.tick() is True
    assert p.tick() is False  # nothing changed, timer not due
    layer.rules.clear()
    assert p.tick() is True
    assert len(p.changes) == 1 and len(p.changes[0]["opened"]) == 2
    assert p.state()["report"]["posture"] == 0.0


def test_service_survives_unreachable_target(cases):
    from shield_redteam.core.target import CallableTarget
    from shield_redteam.core.types import Decision

    def boom() -> str:
        raise ConnectionError("refused")

    p = Poligon(CallableTarget(lambda c: Decision("allow"), config=boom), cases)
    assert p.tick() is False and "unreachable" in p.error


# --- corpus ------------------------------------------------------------------------


def test_shipped_corpus_loads_and_is_balanced():
    cs = corpus.load_dir(ROOT / "corpus")
    attacks = [c for c in cs if c.must_stop]
    benign = [c for c in cs if not c.must_stop]
    assert len(attacks) >= 50 and len(benign) >= 25
    assert {c.direction for c in attacks} >= {"input", "output", "tool_call", "tool_result", "tool_description"}
    assert all(c.owasp for c in attacks)


def test_corpus_rejects_bad_files(tmp_path):
    (tmp_path / "x.yaml").write_text("kind: attack\ncategory: c\ncases:\n  - {id: one}\n")
    with pytest.raises(corpus.CorpusError, match="missing"):
        corpus.load_dir(tmp_path)
    (tmp_path / "x.yaml").write_text("kind: attack\ncategory: c\ncases:\n  - {id: a, text: t}\n  - {id: a, text: u}\n")
    with pytest.raises(corpus.CorpusError, match="duplicate"):
        corpus.load_dir(tmp_path)


# --- signature feed self-test ---------------------------------------------------------


def test_feedcheck_passes_good_signature_and_flags_broken_ones():
    feed = {
        "feed_version": "1",
        "signatures": [
            {"id": "OK", "pattern": "rm -rf", "tests": {"match": ["sudo rm -rf /"], "clean": ["remove the file"]}},
            {"id": "MISSES", "pattern": "rm -rf", "tests": {"match": ["del /s"]}},
            {"id": "OVERFIRES", "pattern": "file", "tests": {"clean": ["remove the file"]}},
            {"id": "BROKEN", "pattern": "(unclosed"},
            {"id": "NOVECTORS", "pattern": "abc"},
        ],
    }
    res = check_feed(feed)
    rows = {r["id"]: r for r in res["rows"]}
    assert rows["OK"]["ok"] and rows["NOVECTORS"]["ok"]
    assert "misses" in rows["MISSES"]["problems"][0]
    assert "fires on clean" in rows["OVERFIRES"]["problems"][0]
    assert "compile" in rows["BROKEN"]["problems"][0]
    assert res["broken"] == 3 and res["self_tested"] == 3


def test_impact_names_the_cause_of_a_change():
    from shield_redteam.poligon.score import _cause

    old = "policy=aaa|feed=1|risk_levels=all normal"
    assert _cause(old, "policy=bbb|feed=1|risk_levels=all normal") == ["policy"]
    assert _cause(old, "policy=aaa|feed=2|risk_levels=alice:watch") == ["feed", "risk_levels"]
    assert _cause(old, old) == []


# --- hands-on actions -------------------------------------------------------------------


def test_impact_names_the_controls_that_changed():
    from shield_redteam.poligon.score import changed_controls

    old = "policy=a|controls=secrets:1block0,pii:1redact0,prompt_injection:1block0"
    new = "policy=b|controls=secrets:0block0,pii:1redact0,prompt_injection:1log0"
    assert changed_controls(old, new) == [
        {"id": "secrets", "what": "switched off"},
        {"id": "prompt_injection", "what": "changed"},
    ]
    assert changed_controls(new, old)[0] == {"id": "secrets", "what": "switched on"}
    assert changed_controls(old, old) == []


def test_write_endpoints_refuse_cross_site_requests(tmp_path):
    from fastapi.testclient import TestClient
    from shield_redteam.config import Config
    from shield_redteam.server.app import create_app

    from tests.conftest import FakeLayer

    (tmp_path / "a.yaml").write_text("kind: attack\ncategory: t\ncases:\n  - {id: a1, text: x marks the spot}\n")
    cfg = Config(tmp_path / "redteam.yaml", {"poligon": {"corpus": str(tmp_path), "mutate": False}})
    client = TestClient(create_app(cfg, target=FakeLayer(["x"]).target(), background=False))
    assert client.post("/api/run", content="{}", headers={"content-type": "text/plain"}).status_code == 415
    evil = {"content-type": "application/json", "origin": "https://evil.example"}
    assert client.post("/api/run", content="{}", headers=evil).status_code == 403
    ok = client.post("/api/run", content="{}", headers={"content-type": "application/json"})
    assert ok.status_code == 200 and ok.json()["posture"] == 100.0
    state = client.get("/api/state").json()
    assert state["poligon"]["runs"] == 1 and "tripwire" not in state
    assert client.get("/api/stage").json()["outcomes"][0]["id"] == "a1"
    assert "Posture score" in client.get("/api/report.md").text
