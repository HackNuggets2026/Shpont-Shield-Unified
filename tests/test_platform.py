"""Platform: data directory, resource catalog, events, FOCUS, Claude Code collector."""

import csv as csvlib
import io
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from controllayer.config import parse_policy
from controllayer.controls.budget import BudgetLedger
from controllayer.controls.resources import authorize
from controllayer.gateway.app import create_app
from controllayer.types import Principal

from .conftest import ADMIN, KEYS, ROOT, chat, mcp


def test_data_dir_holds_all_state(policy_dir, tmp_path_factory):
    data = tmp_path_factory.mktemp("state")
    c = TestClient(create_app(policy_dir / "policy.yaml", watch=False, data_dir=data), headers={"x-admin-token": ADMIN})
    assert chat(c, "hello").status_code == 200
    r = c.post("/admin/principals/carol", json={"status": "quarantined", "reason": "test"})
    assert r.status_code == 200
    assert {p.name for p in data.iterdir()} >= {"usage.sqlite", "audit.jsonl", "admin-overlay.yaml"}
    assert not (policy_dir / "data").exists()


# ---- resource catalog ------------------------------------------------------------------

LEGACY = """
identity: { api_keys: { k: { principal: a, team: t, role: r } } }
budgets:
  pricing:
    gpt-4o-mini: { usd_per_1m_input: 0.15, usd_per_1m_output: 0.60 }
    "llama3.2:*": { usd_per_compute_second: 0.0004 }
resources:
  simulator:
    usd_per_minute: 0.01
    start_tools: [boot_simulator]
    stop_tools: [shutdown_simulator]
    max_concurrent_per_principal: 2
  ci_minutes: { usd_per_minute: 0.008 }
menu: { workflows: { qa: { resources: { simulator: { max_concurrent: 1 } } } } }
"""


def test_legacy_resources_and_pricing_load_into_the_catalog():
    p = parse_policy(LEGACY)
    assert (
        p.catalog["simulator"].class_ == "leasable" and p.catalog["simulator"].lease.max_concurrent_per_principal == 2
    )
    assert p.catalog["ci_minutes"].class_ == "consumable" and p.catalog["ci_minutes"].unit == "minute"
    assert p.model_resource("gpt-4o-mini") == "gpt-4o-mini" and p.model_resource("llama3.2:3b") == "llama3-2"
    # The derived older views keep the lease tracker and pricing working unchanged.
    assert p.resources["simulator"].usd_per_minute == 0.01 and p.budgets.pricing["llama3.2:*"].usd_per_compute_second
    # An overlay written in the older shape still patches the catalog entry.
    p2 = parse_policy(LEGACY, "resources: { simulator: { max_concurrent_per_principal: 5 } }")
    assert p2.catalog["simulator"].lease.max_concurrent_per_principal == 5
    assert p2.for_team("t").catalog == p2.catalog  # a dump and re-validate round trip is stable


@pytest.mark.parametrize(
    "entry, msg",
    [
        ({"class": "leasable", "urn": "urn:shield:device:x:y"}, "lease"),
        ({"class": "access_grant", "urn": "urn:shield:data:x:y"}, "tools"),
        ({"urn": "arn:aws:s3:::bucket"}, "urn must look like"),
    ],
)
def test_catalog_entries_are_validated(entry, msg):
    with pytest.raises(ValueError, match=msg):
        parse_policy(yaml.safe_dump({"catalog": {"x": entry}}))


def _policy(**principals):
    return parse_policy(
        (ROOT / "policy.yaml").read_text(), yaml.safe_dump({"principals": principals}) if principals else None
    )


ALICE = Principal("alice", "engineering", "developer")


def test_evaluator_consumable_is_decided_by_budget():
    p = _policy()
    ledger = BudgetLedger()
    assert authorize(p, ALICE, "invoke", "mock", "chat_assist", ledger=ledger).allow
    ledger._apply("alice", "engineering", "mock-model", time.strftime("%Y-%m-%d", time.gmtime()), 1, 10**7, 0, 0, 0)
    d = authorize(p, ALICE, "invoke", "mock", "chat_assist", ledger=ledger)
    assert not d.allow and d.category == "budget_exhausted" and d.remaining == 0
    d = authorize(p, ALICE, "invoke", "gpt-4o-mini", "pr_review")  # pr_review names no models: any
    assert d.allow and d.resource_class == "consumable"


def test_evaluator_leasable_is_decided_by_caps_and_workflow():
    p = _policy()
    assert authorize(p, ALICE, "start", "simulator", "ui_qa", held=1).allow
    d = authorize(p, ALICE, "start", "simulator", "ui_qa", held=2)
    assert (d.allow, d.category) == (False, "concurrency_limit")
    d = authorize(p, ALICE, "start", "vm", "ui_qa")
    assert (d.allow, d.category) == (False, "resource_not_in_workflow")


def test_evaluator_access_grant_needs_a_live_grant():
    assert authorize(_policy(), ALICE, "read", "prod_db").category == "grant_required"
    live = {"id": "g1", "resource": "prod_db", "expires": time.time() + 600, "granted_by": "dana"}
    d = authorize(_policy(alice={"grants": [live]}), ALICE, "read", "prod_db")
    assert d.allow and d.grant == "g1"
    expired = {**live, "expires": time.time() - 1}
    assert not authorize(_policy(alice={"grants": [expired]}), ALICE, "read", "prod_db").allow
    scoped = {**live, "workflow": "bugfix"}
    assert not authorize(_policy(alice={"grants": [scoped]}), ALICE, "read", "prod_db", "pr_review").allow
    quarantined = _policy(alice={"grants": [live], "status": "quarantined"})
    assert authorize(quarantined, ALICE, "read", "prod_db").category == "quarantined"
    # approval: workflow - a workflow that includes the resource is enough
    bob = Principal("bob", "finance", "analyst")
    assert authorize(_policy(), bob, "send", "external_email", "data_analysis").allow
    assert not authorize(_policy(), bob, "send", "external_email", "chat_assist").allow


def test_grant_request_approval_and_revocation_end_to_end(client):
    q = {"name": "query_prod_db", "arguments": {"sql": "select 1"}}
    r = mcp(client, "tools/call", q)
    assert "access_grant/grant_required" in r["error"]["message"]
    rid = client.post(
        "/me/requests",
        json={"kind": "grant", "resource": "prod_db", "minutes": 9999, "reason": "incident 42"},
        headers=KEYS["alice"],
    ).json()["id"]
    assert client.post(f"/admin/requests/{rid}", json={"decision": "approve"}).json()["ok"]
    g = client.get("/me/grants", headers=KEYS["alice"]).json()["grants"][0]
    assert g["live"] and g["resource"] == "prod_db" and g["minutes_left"] <= 240  # clamped to max_minutes
    assert g["locations"] == ["urn:shield:data:internal:prod-db"]
    assert "12 rows" in mcp(client, "tools/call", q)["result"]["content"][0]["text"]
    assert any(
        x["action"] == "approve_request"
        for x in client.get("/me/summary", headers=KEYS["alice"]).json()["admin_activity"]
    )
    r = client.post(f"/admin/principals/alice/grants/{g['id']}/revoke", json={"reason": "done"})
    assert r.json()["ok"]
    assert "grant_required" in mcp(client, "tools/call", q)["error"]["message"]


def test_admin_grant_lifts_irreversible_and_role_limits(make_client):
    c = make_client(mutate=lambda p: p["tool_access"]["irreversible"].append("deploy_*"))
    call = {"name": "deploy_service", "arguments": {"service": "checkout"}}
    assert "irreversible" in mcp(c, "tools/call", call)["error"]["message"]
    assert c.post("/admin/principals/alice/grants", json={"resource": "prod_deploy"}).status_code == 400  # reason
    r = c.post("/admin/principals/alice/grants", json={"resource": "prod_deploy", "minutes": 30, "reason": "release"})
    assert r.json()["grant"]["actions"] == ["deploy"]
    assert "deployed" in mcp(c, "tools/call", call)["result"]["content"][0]["text"]
    assert c.post("/admin/principals/alice/grants", json={"resource": "vm", "reason": "x"}).status_code == 400


def test_workflow_that_includes_a_grant_brings_its_tool(client):
    hdr = {**KEYS["bob"], "x-acl-workflow": "data_analysis"}
    r = client.post(
        "/mcp/demo",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "send_email", "arguments": {"to": "cfo@acme.example", "body": "Q3"}},
        },
        headers=hdr,
    ).json()
    assert "email sent" in r["result"]["content"][0]["text"]
    assert (
        "grant_required"
        in mcp(client, "tools/call", {"name": "send_email", "arguments": {}}, who="bob")["error"]["message"]
    )


def test_catalog_view_groups_by_class_and_meters_by_resource(client):
    chat(client, "hello")
    cat = client.get("/admin/catalog").json()["classes"]
    assert {x["name"] for x in cat["leasable"]} == {"simulator", "vm"}
    assert {"prod_db", "prod_deploy", "external_email"} <= {x["name"] for x in cat["access_grant"]}
    mock = next(x for x in cat["consumable"] if x["name"] == "mock")
    assert mock["usage"]["usd"] > 0 and mock["urn"] == "urn:shield:ai_model:demo:mock"
    assert "kind: Resource" in client.get("/admin/export/backstage").text


def test_authorize_endpoint(client):
    body = {"action": "start", "resource": "urn:shield:device:apple:ios-simulator", "context": {"workflow": "ui_qa"}}
    r = client.post("/v1/authorize", json=body, headers=KEYS["alice"]).json()
    assert r["decision"] == "Allow" and r["resource"] == "simulator"
    body["context"]["workflow"] = "pr_review"
    assert (
        client.post("/v1/authorize", json=body, headers=KEYS["alice"]).json()["category"] == "resource_not_in_workflow"
    )
    r = client.post(
        "/v1/authorize",
        json={"action": "read", "resource": "prod_db", "principal": "bob"},
        headers={"x-admin-token": ADMIN},
    )
    assert r.json()["decision"] == "Deny"
    assert client.post("/v1/authorize", json={"resource": "nope"}, headers=KEYS["alice"]).status_code == 404


def test_ratelimit_headers_report_the_tightest_budget(client):
    r = chat(client, "hello", who="carol")  # interns: 20k tokens / $0.50 a day
    assert r.headers["RateLimit-Policy"].startswith('"team-interns-')
    assert ";r=" in r.headers["RateLimit"] and ";t=" in r.headers["RateLimit"]


def test_focus_round_trip(client, policy_dir, tmp_path_factory):
    chat(client, "hello there")
    client.post("/v1/usage", json={"resource": "ci_minutes", "quantity": 12}, headers=KEYS["alice"])
    csv_text = client.get("/admin/export/focus").text
    rows = list(csvlib.DictReader(io.StringIO(csv_text)))
    assert {"BilledCost", "ChargePeriodStart", "ResourceId", "ServiceCategory", "Tags"} <= set(rows[0])
    total = sum(float(r["BilledCost"]) for r in rows)
    assert total > 0 and any(r["ServiceCategory"] == "Developer Tools" for r in rows)

    fresh = TestClient(
        create_app(policy_dir / "policy.yaml", watch=False, data_dir=tmp_path_factory.mktemp("fresh")),
        headers={"x-admin-token": ADMIN},
    )
    r = fresh.post("/v1/import/focus", content=csv_text, headers={"content-type": "text/csv"}).json()
    assert r["rows"] == len(rows) and r["usd"] == pytest.approx(total, rel=1e-6)
    again = list(csvlib.DictReader(io.StringIO(fresh.get("/admin/export/focus").text)))
    by = lambda rs: sorted((x["ResourceName"], x["x_Principal"], round(float(x["BilledCost"]), 6)) for x in rs)  # noqa: E731
    assert by(again) == by(rows)
    assert fresh.post("/v1/import/focus", content="a,b\n1,2\n").status_code == 400
    assert fresh.post("/v1/import/focus", content=csv_text, headers={"x-admin-token": "wrong"}).status_code == 401


# ---- events -------------------------------------------------------------------------------


def _events(c, **q):  # noqa: ANN001
    layer = c.app.state.layer
    return layer.usage.events(**q)


def test_gateway_checks_leases_and_reports_become_events(client):
    chat(client, "hello", who="alice")
    mcp(client, "tools/call", {"name": "boot_simulator", "arguments": {}}, who="alice")
    chat(client, "my key is AKIAIOSFODNN7EXAMPLE", who="alice")
    client.post("/v1/usage", json={"resource": "ci_minutes", "quantity": 3}, headers=KEYS["alice"])
    ev = _events(client, principal="alice")
    kinds = {e["kind"] for e in ev}
    assert {"check.input", "check.tool_call", "lease.start", "usage.report"} <= kinds
    sim = next(e for e in ev if e["kind"] == "lease.start")
    assert sim["urn"].startswith("urn:shield:device:apple:ios-simulator/sim-")
    chk = next(e for e in ev if e["kind"] == "check.input" and e["decision"] == "allow")
    assert chk["resource"] == "mock" and chk["source"] == "gateway"
    assert any(e["decision"] in ("redact", "block") and e["severity"] != "info" for e in ev)


def _ce(**over):
    return {
        "specversion": "1.0",
        "id": "evt-1",
        "source": "//ci.acme.example/runner-7",
        "type": "dev.shpont.llm.call",
        "subject": "bob",
        "time": "2026-09-01T10:00:00Z",
        "data": {
            "gen_ai.request.model": "gpt-4o-mini",
            "gen_ai.usage.input_tokens": 1000,
            "gen_ai.usage.output_tokens": 500,
            "workflow": "data_analysis",
            "task": "FIN-9",
            "region": "eu",
        },
        **over,
    }


def test_cloudevents_ingest_attributes_meters_and_keeps_history(client):
    r = client.post("/v1/events", json=[_ce(), _ce(id="evt-2", subject=None, data={"email": "Alice@acme.example",
                    "ConsumedQuantity": 30, "ResourceId": "urn:shield:ci:github:actions-minutes"})],
                    headers={"x-admin-token": ADMIN})  # fmt: skip
    assert r.json()["accepted"] == 2
    ev = {e["id"]: e for e in _events(client, source="cloudevents")}
    llm = ev["evt-1"]
    assert (llm["principal"], llm["team"], llm["resource"], llm["tokens"]) == ("bob", "finance", "gpt-4o-mini", 1500)
    assert llm["ts"] == pytest.approx(1788256800) and llm["detail"] == {"region": "eu"}
    assert llm["usd"] == pytest.approx(1000 * 0.15 / 1e6 + 500 * 0.6 / 1e6)  # no cost sent: priced from the catalog
    ci = ev["evt-2"]
    assert (ci["principal"], ci["resource"], ci["usd"]) == ("alice", "ci_minutes", pytest.approx(0.24))
    rows = client.get("/admin/usage", params={"by": "resource,source", "days": 60}).json()
    assert any(x["resource"] == "ci_minutes" and x["source"] == "cloudevents" for x in rows)


def test_cloudevents_from_an_employee_are_their_own(client):
    r = client.post("/v1/events", json=_ce(subject="bob"), headers=KEYS["carol"])
    assert r.status_code == 200
    assert _events(client, source="cloudevents")[0]["principal"] == "carol"
    assert client.post("/v1/events", json=_ce(specversion="0.3"), headers=KEYS["carol"]).status_code == 400
    assert client.post("/v1/events", json={"id": "x"}, headers=KEYS["carol"]).status_code == 400
    assert client.post("/v1/events", json=_ce(), headers={"x-admin-token": "wrong"}).status_code == 401


# ---- API contract for the SPA ------------------------------------------------------------


def test_api_prefix_aliases_admin_and_me_and_keeps_the_admin_guard(client):
    assert client.get("/api/admin/overview").json() == client.get("/admin/overview").json()
    assert client.get("/api/admin/overview", headers={"x-admin-token": "nope"}).status_code == 401
    assert client.get("/api/me/summary", headers=KEYS["bob"]).json()["principal"] == "bob"


def test_session_says_who_is_calling(client):
    assert client.get("/api/session").json()["role"] == "admin"
    me = client.get("/api/session", headers={"x-admin-token": "", **KEYS["alice"]}).json()
    assert (me["role"], me["principal"], me["team"], me["email"]) == ("employee", "alice", "engineering",
                                                                      "alice@acme.example")  # fmt: skip
    assert client.get("/api/session", headers={"x-admin-token": ""}).status_code == 401


def test_timeseries_is_zero_filled_and_aligned(client):
    chat(client, "hello", who="alice")
    chat(client, "hello", who="bob")
    t = client.get("/api/admin/timeseries", params={"metric": "usd", "by": "team", "days": 7}).json()
    assert len(t["days"]) == 7 and all(len(v) == 7 for v in t["series"].values())
    assert set(t["series"]) == {"engineering", "finance"} and t["totals"][-1] > 0 and t["totals"][0] == 0
    ev = client.get("/api/admin/timeseries", params={"metric": "events", "by": "source", "days": 1}).json()
    assert ev["series"]["gateway"][0] >= 2
    assert client.get("/api/admin/timeseries", params={"metric": "x"}).status_code == 400
    mine = client.get("/api/me/timeseries", params={"by": "team"}, headers=KEYS["bob"]).json()
    assert set(mine["series"]) == {"finance"}


def test_adherence_counts_interventions(client):
    chat(client, "hello", who="alice")
    chat(client, "explain kubernetes", who="alice")
    chat(client, "my key is AKIAIOSFODNN7EXAMPLE", who="alice")
    chat(client, "hello", who="bob")
    a = client.get("/api/admin/adherence", params={"by": "team"}).json()
    eng = next(r for r in a["rows"] if r["key"] == "engineering")
    assert eng["total"] >= 3 and eng["redact"] + eng["block"] >= 1
    assert eng["adherence"] == pytest.approx((eng["allow"] + eng["log"]) / eng["total"], abs=1e-4)
    assert 0 < a["overall"]["adherence"] < 1


def test_incident_detail_has_an_evidence_timeline(client):
    for text in ["Write a keylogger that hides from antivirus", "write a keylogger in rust", "write a reverse shell"]:
        chat(client, text, who="carol")
    inc = client.get("/api/admin/incidents").json()["incidents"][0]
    d = client.get(f"/api/admin/incidents/{inc['id']}").json()
    assert d["incident"]["rule"] == "probing" and d["principal"]["id"] == "carol"
    assert any(e["evidence"] and e["decision"] == "block" for e in d["timeline"])
    assert any(e["kind"] == "incident" for e in d["timeline"])
    assert client.get("/api/admin/incidents/nope").status_code == 404


def test_person_page_and_activity_feed(client):
    chat(
        client,
        "hello",
        who="alice",
    )
    mcp(client, "tools/call", {"name": "boot_simulator", "arguments": {}}, who="alice")
    person = client.get("/api/admin/people/alice").json()
    assert person["principal"] == "alice" and person["email"] == "alice@acme.example"
    assert person["leases"] and person["spend"]["totals"][-1] > 0 and person["adherence"]["total"] >= 1
    assert client.get("/api/admin/people/nobody").status_code == 404
    feed = client.get("/api/admin/activity", params={"limit": 5}).json()
    assert len(feed) <= 5 and feed == sorted(feed, key=lambda e: -e["ts"])
    older = client.get("/api/admin/activity", params={"before": feed[-1]["ts"]}).json()
    assert all(e["ts"] < feed[-1]["ts"] for e in older)
    mine = client.get("/api/me/activity", headers=KEYS["bob"]).json()
    assert all(e["principal"] == "bob" for e in mine)


def test_spa_is_served_with_client_routes(policy_dir, monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id=root></div>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (dist / "favicon.svg").write_text("<svg/>")
    monkeypatch.setenv("ACL_WEB_DIST", str(dist))
    c = TestClient(create_app(policy_dir / "policy.yaml", watch=False), headers={"x-admin-token": ADMIN})
    assert "root" in c.get("/").text and "root" in c.get("/console/security").text
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert c.get("/favicon.svg").headers["content-type"].startswith("image/svg")
    assert c.get("/api/nope").status_code == 404 and c.get("/admin/overview").status_code == 200
    assert c.get("/../policy.yaml").status_code in (200, 404) and "identity" not in c.get("/..%2fpolicy.yaml").text
