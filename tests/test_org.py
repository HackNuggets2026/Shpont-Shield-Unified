"""Enterprise scale: the directory, org units, rollup events and the org endpoints (docs/scale-contract.md)."""

import time

import pytest
from fastapi.testclient import TestClient

from controllayer.config import parse_policy
from controllayer.gateway.app import create_app
from controllayer.usage import UsageStore
from seed.world import seed

from .conftest import ADMIN, ROOT, chat, edit_policy

PEOPLE = 600


@pytest.fixture(scope="module")
def org(tmp_path_factory):
    import shutil

    base = tmp_path_factory.mktemp("org")
    shutil.copy(ROOT / "policy.yaml", base / "policy.yaml")
    shutil.copytree(ROOT / "feeds", base / "feeds")
    stats = seed(base / "policy.yaml", base / "data", days=14, rng_seed=3, now=time.time() - 60, people=PEOPLE)
    app = create_app(base / "policy.yaml", watch=False, data_dir=base / "data")
    return TestClient(app, headers={"x-admin-token": ADMIN}), stats


def test_seeded_org_shape(org):
    c, stats = org
    assert stats["people"] == PEOPLE + 12
    o = c.get("/api/admin/org", params={"days": 14}).json()
    assert o["name"] == "ACME Bank" and o["headcount"] == PEOPLE + 12
    assert o["departments_count"] == 6 and o["teams"] == 41
    assert o["window"]["days"] == 14
    names = [d["name"] for d in o["departments"]]
    assert names[:2] == ["Engineering", "Platform & Security"]
    eng = o["departments"][0]
    assert eng["owner"] == "cto-office" and eng["teams"] == 14
    assert eng["claude_code_users"] > 0 and eng["usd_per_active"] > o["departments"][-1]["usd_per_active"]
    t = o["totals"]
    assert 0.95 < t["adherence"] < 1 and t["checks"] > t["interventions"] > 0
    assert t["usd_prev"] > 0 and t["people_at_risk"] >= 1 and t["incidents_open"] >= 4
    assert sum(d["headcount"] for d in o["departments"]) == o["headcount"]
    assert abs(sum(d["usd"] for d in o["departments"]) - t["usd"]) < 1


def test_teams_sorted_paged_and_filtered(org):
    c, _ = org
    r = c.get("/api/admin/org/teams", params={"limit": 5, "sort": "usd"}).json()
    assert r["total"] == 41 and len(r["rows"]) == 5
    usd = [x["usd"] for x in r["rows"]]
    assert usd == sorted(usd, reverse=True)
    page2 = c.get("/api/admin/org/teams", params={"limit": 5, "offset": 5}).json()["rows"]
    assert not {x["name"] for x in page2} & {x["name"] for x in r["rows"]}
    fin = c.get("/api/admin/org/teams", params={"department": "Finance", "sort": "headcount", "order": "asc"}).json()
    assert fin["total"] == 5 and {x["department"] for x in fin["rows"]} == {"Finance"}
    heads = [x["headcount"] for x in fin["rows"]]
    assert heads == sorted(heads)
    assert c.get("/api/admin/org/teams", params={"sort": "nope"}).status_code == 400


def test_unit_drilldown(org):
    c, _ = org
    d = c.get("/api/admin/org/unit", params={"kind": "department", "name": "Engineering", "days": 14}).json()
    assert d["metrics"]["name"] == "Engineering" and len(d["teams"]) == 14
    assert len(d["spend"]["days"]) == 14 and d["spend"]["series"]
    assert len(d["adherence_trend"]["values"]) == 14
    wf = {w["workflow"]: w for w in d["by_workflow"]}
    assert wf["bugfix"]["runs"] > 0 and wf["bugfix"]["p90"] >= wf["bugfix"]["p50"] > 0
    assert any(r["resource"] == "claude_code" for r in d["by_resource"])
    assert set(d["outliers"]) == {"cost", "risk"}
    t = c.get("/api/admin/org/unit", params={"kind": "team", "name": "engineering"}).json()
    assert t["metrics"]["department"] == "Engineering" and t["teams"] == []
    assert any(x["principal"] == "frank" for x in t["outliers"]["risk"])
    assert c.get("/api/admin/org/unit", params={"kind": "team", "name": "nope"}).status_code == 404


def test_people_search(org):
    c, _ = org
    r = c.get("/api/admin/people", params={"limit": 10}).json()
    assert r["total"] == PEOPLE + 12 and r["rows"][0]["principal"] == "frank"  # riskiest first
    row = r["rows"][0]
    assert {"department", "name", "email", "usd", "tokens", "status", "risk", "budget_scale"} <= set(row)
    alice = c.get("/api/admin/people", params={"q": "alice"}).json()["rows"]
    assert [x["principal"] for x in alice] == ["alice"] and alice[0]["department"] == "Engineering"
    by_mail = c.get("/api/admin/people", params={"q": "ALICE@acme"}).json()["rows"]
    assert by_mail[0]["principal"] == "alice"
    q = c.get("/api/admin/people", params={"status": "quarantined"}).json()
    assert {"frank"} <= {x["principal"] for x in q["rows"]} and all(x["status"] == "quarantined" for x in q["rows"])
    limited = c.get("/api/admin/people", params={"status": "limited"}).json()["rows"]
    assert limited and all(x["budget_scale"] < 1 and x["status"] == "active" for x in limited)
    fin = c.get("/api/admin/people", params={"department": "Finance", "sort": "usd", "limit": 5}).json()
    assert all(x["department"] == "Finance" for x in fin["rows"])
    assert [x["usd"] for x in fin["rows"]] == sorted((x["usd"] for x in fin["rows"]), reverse=True)
    names = c.get("/api/admin/people", params={"sort": "name", "order": "asc", "limit": 20}).json()["rows"]
    assert [x["name"].lower() for x in names] == sorted(x["name"].lower() for x in names)
    assert c.get("/api/admin/people", params={"status": "nope"}).status_code == 400


def test_outliers(org):
    c, _ = org
    o = c.get("/api/admin/outliers", params={"days": 14, "limit": 5}).json()
    assert set(o) == {"cost", "risk", "growth"}
    assert o["cost"] and all(x["ratio"] >= 3 and x["usd"] >= 3 * x["team_median"] - 0.01 for x in o["cost"])
    assert o["risk"][0]["principal"] == "frank" and o["risk"][0]["level"] == "quarantine"
    assert all(x["growth"] > 0 for x in o["growth"])
    eng = c.get("/api/admin/outliers", params={"department": "Engineering"}).json()
    assert all(x["department"] == "Engineering" for k in ("cost", "risk", "growth") for x in eng[k])


def test_department_everywhere(org):
    c, _ = org
    ts = c.get("/api/admin/timeseries", params={"by": "department", "days": 14}).json()
    assert {"Engineering", "Finance", "Sales & Support"} <= set(ts["series"])
    eng = c.get("/api/admin/timeseries", params={"by": "team", "department": "Engineering", "days": 14}).json()
    assert "payments" in eng["series"] and "finance" not in eng["series"]
    adh = c.get("/api/admin/adherence", params={"by": "department", "days": 14}).json()
    assert {r["key"] for r in adh["rows"]} >= {"Engineering", "Operations"}
    one = c.get("/api/admin/adherence", params={"by": "workflow", "team": "payments"}).json()
    assert one["overall"]["total"] < adh["overall"]["total"]
    val = c.get("/api/admin/value", params={"by": "department", "days": 14}).json()["rows"]
    eng_v = next(r for r in val if r["key"] == "Engineering")
    assert eng_v["commits"] > 0 and eng_v["claude_code_usd"] > 0
    use = c.get("/api/admin/usage", params={"by": "team", "days": 14, "department": "Finance"}).json()
    assert {r["team"] for r in use} <= {"finance", "treasury", "risk", "accounting", "tax"}


def test_rollups_count_as_n_and_stay_out_of_the_feed(org):
    c, _ = org
    feed = c.get("/api/admin/activity", params={"limit": 500}).json()
    assert feed and all((e.get("n") or 1) <= 1 for e in feed)
    assert all("department" in e for e in feed)
    checks = c.get("/api/admin/adherence", params={"days": 14}).json()["overall"]["total"]
    store = c.app.state.layer.usage
    rows = store._q("SELECT COUNT(*) rows, SUM(COALESCE(n,1)) n FROM events WHERE kind LIKE 'check.%'")[0]
    assert rows["n"] > rows["rows"] and checks > rows["rows"] * 1.5


def test_live_traffic_gets_a_department(make_client):
    c = make_client()
    assert chat(c, "hello there", who="bob").status_code == 200
    store = c.app.state.layer.usage
    u = store._q("SELECT department FROM usage WHERE principal='bob'")
    e = store._q("SELECT department FROM events WHERE principal='bob'")
    assert u and {r["department"] for r in u + e} == {"Finance"}
    assert store.person("bob")["department"] == "Finance"  # API keys are merged into the directory


def test_a_key_may_name_its_department(policy_dir):
    def mutate(p):
        p["identity"]["api_keys"]["dev-alice-key"]["department"] = "Platform & Security"
        p["identity"]["api_keys"]["ops-agent-key"]["team"] = "skunkworks"

    edit_policy(policy_dir, mutate)
    c = TestClient(create_app(policy_dir / "policy.yaml", watch=False), headers={"x-admin-token": ADMIN})
    store = c.app.state.layer.usage
    assert store.department_of("alice") == "Platform & Security"
    assert store.department_of("ops-agent") == "Unassigned"  # a team listed nowhere
    o = c.get("/api/admin/org").json()
    assert "Unassigned" in [d["name"] for d in o["departments"]]


def test_a_team_in_two_departments_is_rejected():
    text = (ROOT / "policy.yaml").read_text().replace("teams: [finance, treasury", "teams: [payments, treasury")
    with pytest.raises(ValueError, match="payments"):
        parse_policy(text)


def test_migrates_an_older_database(tmp_path):
    import sqlite3

    db = sqlite3.connect(tmp_path / "old.sqlite")
    db.executescript(
        "CREATE TABLE events (ts REAL, id TEXT, source TEXT, kind TEXT, principal TEXT, team TEXT, client TEXT,"
        " session TEXT, prompt_id TEXT, task TEXT, workflow TEXT, resource TEXT, urn TEXT, model TEXT, tool TEXT,"
        " decision TEXT, severity TEXT, usd REAL, tokens INTEGER, request_id TEXT, detail TEXT);"
        "INSERT INTO events (ts, kind, principal, decision) VALUES (1, 'check.input', 'x', 'allow');"
    )
    db.commit()
    db.close()
    s = UsageStore(tmp_path / "old.sqlite")
    assert s.adherence(None, 0)[0]["total"] == 1
    s.add_event({"kind": "check.input", "principal": "x", "decision": "allow", "n": 4})
    assert s.adherence(None, 0)[0]["total"] == 5
