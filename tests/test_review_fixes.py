"""Regression tests for review findings: each test names the input that used to misbehave."""

import csv as csvlib
import gzip
import io
import json
import time

import pytest
from fastapi.testclient import TestClient

from controllayer.gateway.app import create_app

from .conftest import ADMIN, KEYS, chat

TODAY = lambda: time.strftime("%Y-%m-%d", time.gmtime())  # noqa: E731


def _ce(i: str, data: dict) -> dict:
    return {"specversion": "1.0", "id": i, "source": "//ci", "type": "llm.call", "data": data}


def _spent(c, pid: str) -> float:
    return c.app.state.layer.ledger.usage[("principal", pid, TODAY())].usd


@pytest.mark.parametrize("usd", [-1000, "NaN", "Infinity", "-0.5"])
def test_cloudevent_cannot_lower_or_poison_a_budget(client, usd):
    assert client.post("/v1/events", json=_ce("a", {"usd": 3.0}), headers=KEYS["carol"]).status_code == 200
    r = client.post("/v1/events", json=_ce("b", {"usd": usd}), headers=KEYS["carol"])
    assert r.status_code == 400 and "non-negative" in r.json()["error"]
    assert _spent(client, "carol") == 3.0
    assert client.post("/v1/events", json=_ce("c", {"input_tokens": -50}), headers=KEYS["carol"]).status_code == 400
    # A NaN in the global scope used to turn every chat response into a 500 (RateLimit header math).
    assert chat(client, "hello", who="alice").status_code == 200


def test_otlp_cost_that_is_not_a_finite_positive_number_is_dropped(client):
    rec = {"attributes": [{"key": "event.name", "value": {"stringValue": "api_request"}},
                          {"key": "cost_usd", "value": {"stringValue": "NaN"}}]}  # fmt: skip
    neg = {"attributes": [{"key": "event.name", "value": {"stringValue": "api_request"}},
                          {"key": "cost_usd", "value": {"doubleValue": -5}}]}  # fmt: skip
    body = {"resourceLogs": [{"scopeLogs": [{"logRecords": [rec, neg]}]}]}
    assert client.post("/v1/logs", json=body, headers=KEYS["carol"]).status_code == 200
    assert _spent(client, "carol") == 0
    assert chat(client, "hello", who="carol").status_code == 200


def _authorize(c, who: str, action: str, resource: str, workflow: str | None = None) -> dict:
    body = {"action": action, "resource": resource, "context": {"workflow": workflow} if workflow else {}}
    return c.post("/v1/authorize", json=body, headers=KEYS[who]).json()


def test_authorize_does_not_lend_a_workflow_the_caller_may_not_order(client):
    # data_analysis (finance, engineering) includes external_email; an intern used to get Allow by naming it.
    assert _authorize(client, "bob", "send", "external_email", "data_analysis")["decision"] == "Allow"
    d = _authorize(client, "carol", "send", "external_email", "data_analysis")
    assert d["decision"] == "Deny" and d["category"] == "workflow_not_available"
    # load_test needs admin approval per person.
    assert _authorize(client, "alice", "start", "vm", "load_test")["category"] == "workflow_not_available"
    # The gateway agrees: the same workflow header is refused there too.
    r = client.post(
        "/v1/chat/completions",
        json={"model": "mock-model", "messages": [{"role": "user", "content": "hello"}]},
        headers={**KEYS["carol"], "x-acl-workflow": "data_analysis"},
    )
    assert r.status_code == 403


def test_authorize_refuses_new_leases_to_a_quarantined_principal(client):
    assert _authorize(client, "alice", "start", "vm", "bugfix")["decision"] == "Allow"
    client.post("/admin/principals/alice", json={"status": "quarantined", "reason": "test"})
    d = _authorize(client, "alice", "start", "vm", "bugfix")
    assert d["decision"] == "Deny" and d["category"] == "quarantined"


@pytest.mark.parametrize(
    "body",
    [
        {"resourceLogs": ["x"]},
        {"resourceLogs": {"a": 1}},
        {
            "resourceLogs": [
                {"scopeLogs": [{"logRecords": [{"attributes": [{"key": "a", "value": {"intValue": "z"}}]}]}]}
            ]
        },
    ],
)
def test_malformed_otlp_logs_are_a_400_not_a_500(client, body):
    assert client.post("/v1/logs", json=body, headers=KEYS["carol"]).status_code == 400


def test_malformed_otlp_metrics_are_a_400_not_a_500(client):
    m = {"name": "claude_code.commit.count", "sum": {"dataPoints": "x"}}
    body = {"resourceMetrics": [{"scopeMetrics": [{"metrics": [m]}]}]}
    assert client.post("/v1/metrics", json=body, headers=KEYS["carol"]).status_code == 400


def test_otlp_gzip_bomb_and_broken_gzip_are_refused(client):
    bomb = gzip.compress(b"{" + b" " * (40 << 20) + b"}")  # 40 MB of JSON whitespace, ~40 KB on the wire
    r = client.post("/v1/logs", content=bomb, headers={**KEYS["carol"], "content-encoding": "gzip"})
    assert r.status_code == 413
    assert client.post("/v1/logs", content=b"\x1f\x8bnot gzip", headers=KEYS["carol"]).status_code == 400
    ok = gzip.compress(json.dumps({"resourceLogs": []}).encode())
    assert client.post("/v1/logs", content=ok, headers={**KEYS["carol"], "content-encoding": "gzip"}).status_code == 200


def _active_time(value: float, session: str = "s-1") -> dict:
    dp = {
        "attributes": [{"key": "session.id", "value": {"stringValue": session}}],
        "timeUnixNano": str(int(time.time() * 1e9)),
        "asDouble": value,
    }
    m = {"name": "claude_code.active_time.total", "sum": {"aggregationTemporality": 2, "dataPoints": [dp]}}
    return {"resourceMetrics": [{"scopeMetrics": [{"metrics": [m]}]}]}


def _active(c, who: str) -> list[float]:
    rows = c.app.state.layer.usage.events(kind="metric.active_time", principal=who)
    return sorted(e["detail"]["value"] for e in rows)


def test_cumulative_metrics_are_not_counted_again_after_a_restart(make_client):
    c = make_client()
    c.post("/v1/metrics", json=_active_time(50), headers=KEYS["alice"])
    c2 = make_client()  # the gateway restarts on the same data
    c2.post("/v1/metrics", json=_active_time(80), headers=KEYS["alice"])
    assert _active(c2, "alice") == [30, 50]


def test_one_employee_cannot_shift_another_employees_cumulative_baseline(client):
    client.post("/v1/metrics", json=_active_time(50), headers=KEYS["alice"])
    client.post("/v1/metrics", json=_active_time(10_000), headers=KEYS["carol"])  # same session id, her key
    client.post("/v1/metrics", json=_active_time(80), headers=KEYS["alice"])
    assert _active(client, "alice") == [30, 50]


@pytest.mark.parametrize("minutes", ["nan", "inf", -5])
def test_grant_minutes_must_be_a_positive_finite_number(client, minutes):
    body = {"kind": "grant", "resource": "prod_db", "minutes": minutes, "reason": "incident"}
    assert client.post("/me/requests", json=body, headers=KEYS["carol"]).status_code == 400
    r = client.post("/admin/principals/carol/grants", json={"resource": "prod_db", "minutes": minutes, "reason": "x"})
    assert r.status_code == 400
    # A NaN expiry used to make every grant listing (and the catalog) a 500.
    assert client.get("/admin/grants").status_code == 200 and client.get("/admin/catalog").status_code == 200


def test_grant_actions_may_be_a_single_string(client):
    r = client.post("/admin/principals/carol/grants", json={"resource": "prod_db", "actions": "read", "reason": "x"})
    assert r.status_code == 200 and r.json()["grant"]["actions"] == ["read"]


def test_focus_export_keeps_quantities_in_full(client, policy_dir, tmp_path_factory):
    u = client.app.state.layer.usage
    u.add(principal="alice", team="engineering", resource="gpt-4o-mini", model="gpt-4o-mini", requests=1,
          input_tokens=1_234_567, usd=0.185185)  # fmt: skip
    u.add(principal="alice", team="engineering", resource="ci_minutes", quantity=1234.56789, unit="minute", usd=9.87)
    rows = {r["ResourceName"]: r for r in csvlib.DictReader(io.StringIO(client.get("/admin/export/focus").text))}
    assert rows["gpt-4o-mini"]["ConsumedQuantity"] == "1234567"
    assert float(rows["ci_minutes"]["ConsumedQuantity"]) == 1234.56789
    fresh = TestClient(
        create_app(policy_dir / "policy.yaml", watch=False, data_dir=tmp_path_factory.mktemp("fresh")),
        headers={"x-admin-token": ADMIN},
    )
    fresh.post("/v1/import/focus", content=client.get("/admin/export/focus").text)
    tokens = {r["resource"]: r["tokens"] for r in fresh.app.state.layer.usage.cost_rows(0)}
    assert tokens["gpt-4o-mini"] == 1_234_567


def test_a_redelivered_cloudevent_is_not_charged_twice(client):
    ev = _ce("run-42", {"usd": 2.5})
    assert client.post("/v1/events", json=ev, headers=KEYS["carol"]).json()["duplicates"] == 0
    r = client.post("/v1/events", json=[ev, ev], headers=KEYS["carol"]).json()  # a retry, twice
    assert r["accepted"] == 2 and r["duplicates"] == 2
    assert _spent(client, "carol") == 2.5
    # The same id from someone else is their own event, not a duplicate of carol's.
    assert client.post("/v1/events", json=ev, headers=KEYS["bob"]).json()["duplicates"] == 0
    assert _spent(client, "bob") == 2.5


def _incident_on_carol(c) -> None:
    c.app.state.layer.risk.signal("zombie_resource", "carol", "vm idle", ["lease-1"])


def test_incident_events_stay_private_when_the_policy_hides_risk(make_client):
    c = make_client(mutate=lambda d: d["privacy"].update(show_risk_to_employee=False))
    _incident_on_carol(c)
    assert any(e["kind"] == "incident" for e in c.get("/admin/activity", params={"principal": "carol"}).json())
    mine = c.get("/me/activity", headers=KEYS["carol"]).json()
    assert not any(e["kind"] == "incident" for e in mine)
    ts = c.get("/me/timeseries", params={"metric": "events", "by": "kind"}, headers=KEYS["carol"]).json()
    assert "incident" not in ts["series"]
    # With risk shown to employees, they do see it.
    c2 = make_client(mutate=lambda d: d["privacy"].update(show_risk_to_employee=True))
    _incident_on_carol(c2)
    assert any(e["kind"] == "incident" for e in c2.get("/me/activity", headers=KEYS["carol"]).json())
