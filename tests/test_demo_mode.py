"""identity.demo_mode: no credential anywhere, keyless callers act as demo_principal, keys still name their owner."""

import logging

import pytest
import yaml
from fastapi.testclient import TestClient

from controllayer.config import parse_policy
from controllayer.gateway.app import create_app

from .conftest import KEYS, ROOT, chat, guard, mcp

ADMIN_PATHS = ("/admin/summary", "/admin/events", "/admin/audit/export", "/admin/policy", "/admin/risk", "/metrics")
CARD = "card 4111 1111 1111 1111"


def last_principal(c: TestClient) -> str:
    return c.get("/admin/events", params={"limit": 1}).json()[0]["principal"]


def test_shipped_policy_is_in_demo_mode_as_alice():
    identity = parse_policy((ROOT / "policy.yaml").read_text()).identity
    assert (identity.demo_mode, identity.demo_principal) == (True, "alice")


def test_admin_needs_no_token(demo_client, client):
    for path in ADMIN_PATHS:
        assert demo_client.get(path).status_code == 200, path
        assert demo_client.get(path, headers={"x-admin-token": "wrong"}).status_code == 200, path
        assert client.get(path, headers={"x-admin-token": ""}).status_code == 401, path
    assert demo_client.get("/admin/summary").json()["demo_mode"] is True
    assert client.get("/admin/summary").json()["demo_mode"] is False


def test_keyless_gateway_calls_act_as_the_demo_principal(demo_client):
    r = chat(demo_client, "hello", who="nobody")
    assert r.status_code == 200, r.text
    assert last_principal(demo_client) == "alice"

    assert guard(demo_client, "hello", who="nobody").json()["action"] == "allow"
    assert last_principal(demo_client) == "alice"

    tools = mcp(demo_client, "tools/list", who="nobody")
    assert "result" in tools, tools
    assert last_principal(demo_client) == "alice"

    m = demo_client.post(
        "/v1/messages",
        json={"model": "claude-sonnet-5", "max_tokens": 64, "messages": [{"role": "user", "content": "hello"}]},
    )
    assert m.status_code == 200, m.text
    assert last_principal(demo_client) == "alice"


def test_an_unknown_key_also_acts_as_the_demo_principal(demo_client):
    assert chat(demo_client, "hello", who="nobody").status_code == 200
    r = demo_client.post(
        "/v1/guard", json={"text": "hello"}, headers={"Authorization": "Bearer sk-ant-not-a-gateway-key"}
    )
    assert r.status_code == 200 and last_principal(demo_client) == "alice"


def test_a_supplied_key_still_names_its_owner(demo_client):
    # Finance blocks card numbers; engineering (alice, the keyless default) only redacts them.
    assert guard(demo_client, CARD, who="nobody").json()["action"] == "redact"
    assert guard(demo_client, CARD, who="bob").status_code == 403
    assert last_principal(demo_client) == "bob"
    assert chat(demo_client, "hello", who="carol").status_code == 200
    assert last_principal(demo_client) == "carol"
    r = demo_client.post("/v1/guard", json={"text": "hello"}, headers={"x-api-key": "alice-agent-key"})
    assert r.status_code == 200 and last_principal(demo_client) == "alice-coder"


def test_keyless_calls_are_refused_without_demo_mode(client):
    assert chat(client, "hello", who="nobody").status_code == 401
    assert guard(client, "hello", who="nobody").status_code == 401
    assert "error" in mcp(client, "tools/call", {"name": "read_file", "arguments": {"path": "x"}}, who="nobody")


def test_panel_shows_anyone_without_a_key(demo_client):
    people = demo_client.get("/me/api/people").json()["people"]
    assert {"principal": "bob", "team": "finance"} in people and all(p["principal"] != "alice-coder" for p in people)
    assert demo_client.get("/me/api/profile").json()["principal"] == "alice"
    assert demo_client.get("/me/api/profile", headers={"x-acl-as": "bob"}).json()["principal"] == "bob"
    assert demo_client.get("/me/api/profile", headers={"x-acl-as": "bob", **KEYS["carol"]}).json()["principal"] == (
        "carol"
    )
    assert demo_client.get("/me/api/profile", headers={"x-acl-as": "alice-coder"}).status_code == 403  # agents never
    assert demo_client.get("/me/api/profile", headers={"x-acl-as": "mallory"}).status_code == 404


def test_signal_names_its_integration_and_stays_capped(demo_client, monkeypatch):
    monkeypatch.delenv("ACL_WAZUH_TOKEN", raising=False)
    body = {"level": "restricted", "ttl_seconds": 600, "source": "usb"}
    r = demo_client.post("/admin/risk/bob/signal", json={**body, "integration": "wazuh"})
    assert r.status_code == 200, r.text
    assert (r.json()["source"], r.json()["requested"], r.json()["level"]) == ("wazuh/usb", "restricted", "watch")
    long = demo_client.post("/admin/risk/bob/signal", json={**body, "integration": "wazuh", "ttl_seconds": 1e9})
    assert long.status_code == 400  # wazuh max_ttl_hours
    for named in (None, "edr", ["wazuh"]):
        r = demo_client.post("/admin/risk/bob/signal", json={**body, "integration": named})
        assert r.status_code == 400 and "wazuh" in r.text, named


@pytest.mark.parametrize(
    "identity, error",
    [
        ({"demo_mode": True, "demo_principal": None}, "needs identity.demo_principal"),
        ({"demo_principal": "alice-coder"}, "not a human principal"),
        ({"demo_principal": "mallory"}, "not a human principal"),
    ],
)
def test_demo_principal_must_be_a_known_human(identity, error):
    data = yaml.safe_load((ROOT / "policy.yaml").read_text())
    data["identity"].update(identity)
    with pytest.raises(ValueError, match=error):
        parse_policy(yaml.safe_dump(data))


def test_demo_mode_is_off_by_default():
    assert parse_policy("identity: {api_keys: {k: {principal: a, team: t, role: r}}}").identity.demo_mode is False


def test_startup_warns_in_demo_mode(policy_dir, caplog):
    with caplog.at_level(logging.WARNING, logger="controllayer.config"):
        create_app(policy_dir / "policy.yaml", watch=False)
    assert not any("DEMO MODE" in r.message for r in caplog.records)
    (policy_dir / "policy.yaml").write_text(
        (policy_dir / "policy.yaml").read_text().replace("demo_mode: false", "demo_mode: true")
    )
    with caplog.at_level(logging.WARNING, logger="controllayer.config"):
        create_app(policy_dir / "policy.yaml", watch=False)
    assert any(
        r.levelno == logging.WARNING and "DEMO MODE" in r.message and "'alice'" in r.message for r in caplog.records
    )
