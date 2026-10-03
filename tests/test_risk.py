"""Insider risk: scores, levels, stricter enforcement while watched, silent alerts."""

import json
import time

from .conftest import KEYS, chat, edit_policy, guard

CARD = "card 4111 1111 1111 1111"


def score(client, pid):
    rows = {r["principal"]: r for r in client.get("/admin/risk").json()["principals"]}
    return rows.get(pid)


def test_findings_accumulate_risk(client):
    guard(client, "key AKIAIOSFODNN7EXAMPLE")  # block: 10 points
    guard(client, CARD)  # redact: 3 points
    assert score(client, "alice")["score"] == 13


def test_score_decays(client):
    guard(client, "key AKIAIOSFODNN7EXAMPLE")
    risk = client.app.state.layer.risk
    s, at = risk._scores["alice"]
    risk._scores["alice"] = (s, at - 24 * 3600)  # one half-life ago
    assert score(client, "alice")["score"] == 5


def test_watch_level_tightens_policy_and_captures_full_text(client, policy_dir):
    assert guard(client, CARD + " (before)").json()["action"] == "redact"
    for i in range(3):  # 3 blocks: 30 points -> watch
        guard(client, f"key AKIAIOSFODNN7EXAMP{i}A")
    assert score(client, "alice")["level"] == "watch"
    assert guard(client, CARD + " (after)").json()["action"] == "block"  # watch_controls: pii mode block
    events = [json.loads(line) for line in (policy_dir / "data/audit.jsonl").read_text().splitlines()]
    assert any(e.get("raw_text", "").endswith("(after)") for e in events)


def test_level_change_raises_silent_alert(client, policy_dir):
    for i in range(4):  # 40 points (decay starts at once, so exactly 30 would sit just below the line)
        r = guard(client, f"key AKIAIOSFODNN7EXAMP{i}A")
    assert "alert" not in json.dumps(r.json())  # the employee's response says nothing
    alerts = client.get("/admin/alerts").json()["alerts"]
    assert any("normal -> watch" in a["reason"] for a in alerts)
    sink = (policy_dir / "data/security-alerts.jsonl").read_text()
    assert "normal -> watch" in sink


def test_alert_category_fires_immediately(client):
    guard(client, "-----BEGIN RSA PRIVATE KEY-----\nMIIE")
    assert any("private_key" in a["reason"] for a in client.get("/admin/alerts").json()["alerts"])


def test_restricted_blocks_everything_until_cleared(client):
    r = client.post("/admin/risk/alice", json={"level": "restricted", "reason": "investigation 42"})
    assert r.status_code == 200
    rr = chat(client, "hello")
    assert rr.status_code == 403 and "insider_risk/restricted" in rr.json()["error"]["message"]
    client.post("/admin/risk/alice", json={"level": "normal", "reset_score": True})
    assert chat(client, "hello again").status_code == 200


def test_agent_inherits_owner_level_and_owner_shares_agent_risk(client):
    client.post("/admin/risk/alice", json={"level": "restricted"})
    r = client.post("/v1/guard", json={"text": "hi"}, headers={"x-api-key": "alice-agent-key"})
    assert r.status_code == 403
    client.post("/admin/risk/alice", json={"level": "normal"})
    client.post("/v1/guard", json={"text": "key AKIAIOSFODNN7EXAMPLE"}, headers={"x-api-key": "alice-agent-key"})
    assert score(client, "alice-coder")["score"] == 10
    assert score(client, "alice")["score"] == 5


def test_rate_limits_and_outages_are_not_evidence(make_client):
    c = make_client(mutate=lambda p: p["budgets"]["per_team"].update(engineering={"requests_per_minute": 1}))
    chat(c, "a")
    chat(c, "b")  # 429
    assert score(c, "alice") is None


def test_risk_admin_requires_token_and_valid_input(client):
    assert client.get("/admin/risk", headers={"x-admin-token": "nope"}).status_code == 401
    assert client.post("/admin/risk/alice", json={"level": "jail"}).status_code == 400
    assert client.post("/admin/risk/mallory", json={"level": "watch"}).status_code == 404


def test_manual_watch_persists(client, policy_dir):
    client.post("/admin/risk/bob", json={"level": "watch", "reason": "tip from HR"})
    state = json.loads((policy_dir / "data/state.json").read_text())
    assert state["watch"]["bob"]["level"] == "watch"


def test_webhook_sink_receives_alert(make_client, policy_dir):
    import httpx
    from fastapi.testclient import TestClient

    from controllayer.gateway.app import create_app

    got = []
    edit_policy(
        policy_dir, lambda p: p["insider_risk"].update(sinks=[{"type": "webhook", "url": "http://siem.example/hook"}])
    )
    transport = httpx.MockTransport(lambda r: got.append(json.loads(r.content)) or httpx.Response(200))
    app = create_app(policy_dir / "policy.yaml", watch=False, upstream_client=httpx.AsyncClient(transport=transport))
    c = TestClient(app, headers={"x-admin-token": "demo-admin-token"})
    c.post("/v1/guard", json={"text": "-----BEGIN RSA PRIVATE KEY-----\nx"}, headers=KEYS["alice"])
    deadline = time.time() + 2
    while not got and time.time() < deadline:
        time.sleep(0.05)
    assert got and got[0]["principal"] == "alice"
