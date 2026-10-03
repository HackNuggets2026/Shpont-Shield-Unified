"""Console analytics: service call prices, per-person budgets, the people directory, usage history and
the /admin/analytics endpoints."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from controllayer import seed
from controllayer.analytics import DAY, HOUR, Buckets, UsageHistory, _bins
from controllayer.config import parse_policy
from controllayer.controls.budget import BudgetLedger
from controllayer.gateway.app import create_app
from controllayer.types import Context, Direction, Principal

from .conftest import ROOT, chat
from .test_resources import AGENT, call, grant

POLICY = parse_policy((ROOT / "policy.yaml").read_text())


def test_service_call_price_per_tool_and_default():
    ledger = BudgetLedger()
    p = Principal("alice", "engineering", "developer")

    def charge(service, tool):
        return ledger.record_call(Context(p, Direction.TOOL_CALL, "", tool=tool), POLICY, service)

    assert charge("heroku", "heroku_scale_formation") == 0.05
    assert charge("heroku", "heroku_get_logs") == 0.004
    assert charge("snowflake", "snowflake_query") == 0.04
    day = time.strftime("%Y-%m-%d", time.gmtime())
    assert ledger.by_service[("heroku", day)].requests == 2
    assert ledger.usage[("principal", "alice", day)].usd == pytest.approx(0.05 + 0.004 + 0.04)


def test_unknown_service_or_tool_price_is_rejected(policy_dir):
    with pytest.raises(ValueError, match="unknown service 'stripee'"):
        parse_policy("budgets: {services: {stripee: {usd_per_call: 1}}}")
    with pytest.raises(ValueError, match="unknown \\['stripe_refunds'\\]"):
        parse_policy("budgets: {services: {stripe: {tools: {stripe_refunds: 1}}}}")


def test_brokered_call_is_charged_to_agent_and_rolled_up_to_owner(client):
    assert grant(client).status_code == 200
    assert "result" in call(client, "github_list_issues", {"repo": "acme/web"}, AGENT)
    assert "result" in call(client, "github_get_file", {"repo": "acme/web", "path": "README.md"}, AGENT)
    person = client.get("/admin/analytics/person/alice?period=1").json()
    item = next(i for i in person["items"] if i["key"] == "service:github")
    assert item["usd"] == pytest.approx(0.002) and item["name"] == "GitHub"
    assert item["by_principal"] == {"alice-coder": pytest.approx(0.002)}
    snap = client.get("/admin/summary").json()["budgets"]["scopes"]
    agent_row = next(r for r in snap if r["key"] == "alice-coder")
    assert agent_row["usd"] == pytest.approx(0.002)  # counts against the agent's budget too
    res = client.get("/admin/analytics/resources?period=1").json()["resources"]
    gh = next(r for r in res if r["id"] == "github-acme")
    assert (gh["calls"], gh["usd"], gh["usd_per_call"]) == (2, pytest.approx(0.002), 0.001)


def test_refused_call_is_not_charged(client):
    call(client, "github_list_issues", {"repo": "acme/web"}, AGENT)  # no grant
    person = client.get("/admin/analytics/person/alice?period=1").json()
    assert person["items"] == [] and person["spend"]["usd"] == 0


def test_per_person_budget_overrides_default_field_by_field_and_is_enforced(make_client):
    def mutate(d):
        d["budgets"]["per_principal"] = {"tokens_per_day": 200000, "usd_per_day": 10}
        d["budgets"]["per_person"] = {"alice": {"usd_per_day": 1e-7}}

    client = make_client(mutate=mutate)
    policy = client.app.state.store.policy
    assert policy.budgets.limits_for("alice").usd_per_day == 1e-7
    assert policy.budgets.limits_for("alice").tokens_per_day == 200000  # kept from per_principal
    assert policy.budgets.limits_for("bob").usd_per_day == 10
    assert chat(client, "hello there").status_code == 200  # spends past the cap
    r = chat(client, "hello again")
    assert r.status_code == 429 and "principal:alice" in r.json()["error"]["message"]
    assert chat(client, "hello there", who="bob").status_code == 200
    person = client.get("/admin/analytics/person/alice?period=1").json()
    # Her allowance is her cap plus her agent's (per_principal's).
    assert (person["person"]["cap"], person["spend"]["daily_cap"]) == (1e-7, pytest.approx(10 + 1e-7))
    assert person["spend"]["today_left"] == pytest.approx(10 + 1e-7 - person["spend"]["today_usd"])


def write_directory(policy_dir, keys, budgets=None):
    (policy_dir / "data").mkdir(exist_ok=True)
    (policy_dir / "data" / "org.json").write_text(json.dumps({"api_keys": keys, "budgets": budgets or {}}))


def test_directory_people_authenticate_and_change_the_policy_version(make_client, policy_dir):
    before = make_client().app.state.store.policy.version
    write_directory(
        policy_dir,
        {"k-dana": {"principal": "dana", "name": "Dana Ito", "team": "finance", "role": "analyst"}},
        {"dana": {"usd_per_day": 7}},
    )
    client = make_client()
    policy = client.app.state.store.policy
    assert policy.version != before
    assert policy.budgets.limits_for("dana").usd_per_day == 7
    r = client.post("/v1/guard", json={"text": "hi"}, headers={"Authorization": "Bearer k-dana"})
    assert r.status_code == 200
    rows = client.get("/admin/analytics/people?q=dana").json()["rows"]
    assert [(r["id"], r["name"], r["budget"]) for r in rows] == [("dana", "Dana Ito", 7 * 30)]


def test_directory_may_not_redefine_policy_keys(make_client, policy_dir):
    write_directory(policy_dir, {"dev-alice-key": {"principal": "eve", "team": "x", "role": "y"}})
    with pytest.raises(ValueError, match="already in the policy"):
        make_client()


def test_without_directory_setting_the_file_is_ignored(make_client, policy_dir):
    write_directory(policy_dir, {"k-dana": {"principal": "dana", "team": "finance", "role": "analyst"}})
    client = make_client(mutate=lambda d: d["identity"].pop("directory"))
    assert "dana" not in {k.principal for k in client.app.state.store.policy.identity.api_keys.values()}


def test_buckets_ring_drops_what_falls_out_and_clears_reused_slots():
    b = Buckets(DAY, 3, ("usd",))
    b.add(("a", "x"), 10 * DAY, (1.0,))
    b.add(("a", "x"), 11 * DAY, (2.0,))
    b.add(("a", "x"), 13 * DAY, (4.0,))  # takes day 10's slot
    assert b.series(("a", "x"), 10, 13, "usd") == [0.0, 2.0, 0.0, 4.0]
    b.add(("a", "x"), 10 * DAY, (100.0,))  # older than the ring holds
    assert b.totals(0, 99, "usd") == [6.0]
    b.add(("a", "x"), 20 * DAY, (8.0,))  # a jump past the whole ring
    assert b.series(("a", "x"), 11, 20, "usd") == [0.0] * 9 + [8.0]


def test_history_round_trips():
    h = UsageHistory()
    now = time.time()
    h.charge("a", "model:m", now, 2, 100, 0.5)
    h.charge("a", "service:s", now - 3 * HOUR, 1, 0, 0.25)
    h.decision("a", now, "block", 3)
    h.score("a", now, 12.5)
    again = UsageHistory()
    again.load(h.dump())
    today = int(now // DAY)
    assert again.daily.series(("a", "model:m"), today, today, "usd") == [0.5]
    assert again.daily.series(("*", "service:s"), today - 1, today, "requests")[-1] + again.daily.series(
        ("*", "service:s"), today - 1, today, "requests"
    )[0] == pytest.approx(1)
    assert again.decisions.series(("a", ""), today, today, "block") == [3.0]
    assert again.scores["a"][today] == 12.5


def test_bins_are_1_2_5_and_cover_the_range():
    assert _bins([0.3, 7.0, 7.0, 64]) == [0.2, 0.5, 1, 2, 5, 10, 20, 50, 100]
    assert _bins([5.0]) == [5, 10]


@pytest.fixture(scope="module")
def org(tmp_path_factory):
    """A seeded 120-person org, its gateway and its history."""
    d = tmp_path_factory.mktemp("org")
    (d / "policy.yaml").write_text((ROOT / "policy.yaml").read_text())
    (d / "feeds").mkdir()
    (d / "feeds" / "signatures.json").write_text((ROOT / "feeds" / "signatures.json").read_text())
    policy = parse_policy((d / "policy.yaml").read_text())
    out = seed.build(policy, 120, 30, 7, time.time())
    (d / "data").mkdir()
    (d / "data" / "org.json").write_text(json.dumps(out["directory"]))
    (d / "data" / "history.json").write_text(json.dumps(out["history"]))
    client = TestClient(create_app(d / "policy.yaml", watch=False), headers={"x-admin-token": "demo-admin-token"})
    return client, out


def expected_spend(out, days):
    """Per human (own + agents), straight from the seed file's daily rows."""
    today = int(time.time() // DAY)
    owner = {k["principal"]: k.get("owner") for k in out["directory"]["api_keys"].values()}
    spend = {}
    for pid, _item, bucket, _req, _tok, usd in out["history"]["history"]["daily"]:
        if pid != "*" and today - days < bucket <= today:
            who = owner.get(pid) or pid
            spend[who] = spend.get(who, 0) + usd
    return spend


def test_people_sorted_paginated_and_matching_the_history(org):
    client, out = org
    expected = expected_spend(out, 7)
    humans = sorted(k["principal"] for k in out["directory"]["api_keys"].values() if "owner" not in k)
    seen, page = [], 1
    while True:
        r = client.get(f"/admin/analytics/people?period=7&per_page=25&page={page}").json()
        seen += r["rows"]
        if page == r["pages"]:
            break
        page += 1
    assert r["total"] == len(seen) == len(humans) + 4  # the policy's own four people
    assert sorted(x["id"] for x in seen if x["id"] in expected or x["usd"]) == sorted(expected)
    usd = [x["usd"] for x in seen]
    assert usd == sorted(usd, reverse=True)
    for x in seen:
        assert x["usd"] == pytest.approx(expected.get(x["id"], 0), abs=1e-4)
    top = seen[0]
    assert top["top_item"]["usd"] == max(top["top_item"]["usd"], 0) and top["top_item"]["key"].count(":") >= 1
    asc = client.get("/admin/analytics/people?period=7&sort=name&per_page=200").json()["rows"]
    assert [x["name"].lower() for x in asc] == sorted(x["name"].lower() for x in asc)


def test_people_filters(org):
    client, out = org
    team = client.get("/admin/analytics/people?team=sales&per_page=200").json()
    assert team["total"] > 0 and {x["team"] for x in team["rows"]} == {"sales"}
    over = client.get("/admin/analytics/people?budget=over&per_page=200").json()["rows"]
    assert over and all(x["used"] >= 1 for x in over)
    under = client.get("/admin/analytics/people?budget=under&per_page=200").json()["rows"]
    assert all(x["used"] < 0.5 for x in under)
    nocap = client.get("/admin/analytics/people?budget=nocap").json()["rows"]
    assert {x["id"] for x in nocap} == {"alice", "bob", "ops-agent", "carol"}
    name = next(k["name"] for k in out["directory"]["api_keys"].values() if "owner" not in k)
    found = client.get("/admin/analytics/people", params={"q": name.split()[1].upper()}).json()["rows"]
    assert name in {x["name"] for x in found}
    elevated = client.get("/admin/analytics/people?risk=elevated&per_page=200").json()["rows"]
    assert elevated and all(x["level"] != "normal" for x in elevated)
    agents = client.get("/admin/analytics/people?kind=agent&per_page=200").json()
    assert agents["total"] == sum(1 for k in out["directory"]["api_keys"].values() if "owner" in k) + 2
    band = client.get("/admin/analytics/people?min_usd=1&max_usd=2&per_page=200").json()["rows"]
    assert all(1 <= x["usd"] < 2 for x in band)
    stripe = client.get("/admin/analytics/people?item=service:salesforce&sort=-item&per_page=200").json()["rows"]
    assert stripe and [x["item_usd"] for x in stripe] == sorted((x["item_usd"] for x in stripe), reverse=True)


def test_overview_totals_agree_with_people_and_history(org):
    client, out = org
    o = client.get("/admin/analytics/overview?period=30").json()
    expected = expected_spend(out, 30)
    assert o["totals"]["usd"] == pytest.approx(sum(expected.values()), rel=1e-6)
    assert o["totals"]["usd_models"] + o["totals"]["usd_services"] == pytest.approx(o["totals"]["usd"])
    assert sum(o["series"]["models"]) + sum(o["series"]["services"]) == pytest.approx(o["totals"]["usd"])
    assert sum(i["usd"] for i in o["items"]) == pytest.approx(o["totals"]["usd"])
    assert [i["usd"] for i in o["items"]] == sorted((i["usd"] for i in o["items"]), reverse=True)
    assert sum(b["count"] for b in o["spend"]["histogram"]) == sum(1 for v in expected.values() if v > 0)
    assert sum(o["budget_bands"].values()) == o["totals"]["people"] == 124
    over = client.get("/admin/analytics/people?budget=over").json()["total"]
    assert o["budget_bands"]["over"] == over
    assert o["totals"]["prev_usd"] is None  # the seed holds no earlier 30 days to compare with
    week = client.get("/admin/analytics/overview?period=7").json()["totals"]
    assert week["prev_usd"] > 0
    assert o["spend"]["p50"] <= o["spend"]["p90"] <= o["spend"]["p99"]
    assert o["spend"]["top"][0]["usd"] == pytest.approx(max(expected.values()))


def test_person_detail_splits_spend_by_item_and_agent(org):
    client, out = org
    owner = next(k["owner"] for k in out["directory"]["api_keys"].values() if "owner" in k)
    p = client.get(f"/admin/analytics/person/{owner}?period=30").json()
    assert p["spend"]["usd"] == pytest.approx(expected_spend(out, 30).get(owner, 0), abs=1e-4)
    assert sum(sum(v) for v in p["series"]["values"]) == pytest.approx(p["spend"]["usd"], abs=1e-6)
    assert p["agents"] and all(a["id"].startswith(owner) for a in p["agents"])
    assert len(p["risk"]["history"]) == 30
    assert client.get("/admin/analytics/person/nobody").status_code == 404


def test_bad_parameters_are_named(client):
    r = client.get("/admin/analytics/people?sort=spend")
    assert r.status_code == 400 and "sort must be one of" in r.json()["error"]
    assert client.get("/admin/analytics/people?risk=high").status_code == 400
    assert client.get("/admin/analytics/overview?period=0").status_code == 422
    assert client.get("/admin/analytics/overview?end=2026-13-45").status_code == 400
    assert client.get("/admin/analytics/people?min_usd=nan").status_code == 422


def test_analytics_need_the_admin_token(client):
    for path in ("overview", "people", "person/alice", "resources"):
        assert client.get(f"/admin/analytics/{path}", headers={"x-admin-token": "wrong"}).status_code == 401


def test_security_grants_for_an_owner_within_their_entitlement(client):
    r = client.post(
        "/admin/grants", json={"agent": "alice-coder", "resource": "github-acme", "scopes": ["read"], "hours": 2}
    )
    assert r.status_code == 200 and r.json()["granted_by"] == "alice"
    assert "result" in call(client, "github_list_issues", {"repo": "acme/web"}, AGENT)
    r = client.patch("/admin/grants/alice-coder/github-acme", json={"scopes": ["read", "write"]})
    assert r.json()["scopes"] == ["read", "write"]
    refused = client.post("/admin/grants", json={"agent": "alice-coder", "resource": "stripe", "scopes": ["read"]})
    assert refused.status_code == 400 and "not entitled" in refused.json()["error"]
    assert client.post("/admin/grants", json={"agent": "alice", "resource": "slack"}).status_code == 404
    notes = [n for n in client.app.state.layer.audit.notes if n["actor"] == "security"]
    assert [n["kind"] for n in notes] == ["grant", "grant_scopes"]


def test_people_summary_and_facet_counts(org):
    client, _ = org
    everyone = client.get("/admin/analytics/people?per_page=200").json()
    s = everyone["summary"]
    assert s["usd"] == pytest.approx(sum(r["usd"] for r in everyone["rows"]))
    assert sum(s["bands"].values()) == sum(s["levels"].values()) == everyone["total"]
    over = client.get("/admin/analytics/people?budget=over&per_page=200").json()
    assert over["total"] == s["bands"]["over"]
    assert over["summary"]["bands"] == s["bands"]  # the budget facet ignores the budget filter itself
    assert sum(over["summary"]["levels"].values()) == over["total"]
    watch = client.get("/admin/analytics/people?risk=watch&per_page=200").json()
    assert watch["total"] == s["levels"]["watch"] and watch["summary"]["levels"] == s["levels"]
    assert [i["usd"] for i in everyone["items"]] == sorted((i["usd"] for i in everyone["items"]), reverse=True)
