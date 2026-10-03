"""Regression tests for review findings: each test names the input that used to misbehave."""

import time

import pytest

from .conftest import KEYS, chat

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
