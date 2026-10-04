"""OCSF / ECS export of decisions, admin actions and alerts; the alert sink formats."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from controllayer.gateway.app import create_app

from .conftest import ADMIN, KEYS, edit_policy, guard

SCHEMA = json.loads((Path(__file__).parent / "ocsf_1.9.0_schema.json").read_text())
AGENT = {"Authorization": "Bearer alice-agent-key"}
IBAN = "PL61 1090 1014 0000 0712 1981 2874"
EMAIL = "bob.smith@example.com"
AWS = "AKIAIOSFODNN7EXAMPLE"


@pytest.fixture
def gw(policy_dir) -> TestClient:
    return TestClient(
        create_app(policy_dir / "policy.yaml", watch=False),
        headers={"x-admin-token": ADMIN},
        client=("10.1.2.3", 50000),
    )


def export(c: TestClient, fmt: str) -> list[dict]:
    r = c.get(f"/admin/audit/export?format={fmt}")
    assert r.status_code == 200 and r.headers["content-type"] == "application/x-ndjson"
    return [json.loads(line) for line in r.text.splitlines()]


def chat_as(c: TestClient, headers: dict, text: str):
    return c.post(
        "/v1/chat/completions",
        json={"model": "mock-model", "messages": [{"role": "user", "content": text}]},
        headers=headers,
    )


def conforms(doc: dict, table: dict, path: str = "") -> list[str]:
    """Unknown attributes and missing required ones, against the OCSF 1.9.0 schema extract."""
    problems = [f"{path}{k} missing" for k in table["required"] if k not in doc]
    for k, v in doc.items():
        if k not in table["attrs"]:
            problems.append(f"{path}{k} unknown")
            continue
        kind = table["attrs"][k]
        if kind and kind != "object":  # json_t / unmapped are free-form
            for item in v if isinstance(v, list) else [v]:
                problems += conforms(item, SCHEMA["objects"][kind], f"{path}{k}.")
    return problems


def test_decisions_map_to_api_activity_with_ai_fields(gw):
    chat_as(gw, KEYS["alice"], f"pay {IBAN} please")
    chat_as(gw, AGENT, "summarise the release notes")
    human, _, agent, _ = [e for e in export(gw, "ocsf") if e["class_uid"] == 6003]

    assert (human["type_uid"], human["activity_id"], human["category_uid"]) == (600301, 1, 6)
    assert human["actor"]["user"] == {
        "uid": "alice",
        "name": "alice",
        "type_id": 1,
        "type": "User",
        "groups": [{"name": "engineering", "type": "team"}, {"name": "developer", "type": "role"}],
    }
    assert human["ai_model"] == {"name": "mock-model", "ai_provider": "mock"}
    assert "ai_agent" not in human and "delegation" not in human
    assert (human["action_id"], human["disposition_id"], human["severity_id"]) == (4, 11, 3)  # redact
    assert human["message_context"]["prompt_text"] == "pay [REDACTED:iban] please"
    assert human["message_context"]["ai_role_id"] == 1
    assert human["src_endpoint"] == {"ip": "10.1.2.3"}
    assert human["metadata"]["version"] == "1.9.0"
    assert set(human["metadata"]["profiles"]) == {"security_control", "ai_operation"}

    # An agent acts in its owner's user context; the agent and the delegation are named separately.
    assert agent["actor"]["user"]["uid"] == "alice"
    assert agent["ai_agent"]["uid"] == "alice-coder"
    assert agent["ai_agent"]["ai_model"] == {"name": "mock-model", "ai_provider": "mock"}
    assert "ai_model" not in agent  # carried in ai_agent.ai_model for agent-mediated operations
    assert agent["delegation"] == {"uid": "alice/alice-coder", "issuer_uid": "controllayer"}
    assert agent["unmapped"]["principal"] == "alice-coder"


def test_model_reply_is_response_text(gw):
    chat_as(gw, KEYS["alice"], "hello")
    reply = next(e for e in export(gw, "ocsf") if e.get("api", {}).get("operation") == "chat/output")
    assert reply["activity_id"] == 2 and reply["message_context"]["ai_role_id"] == 2
    assert "response_text" in reply["message_context"] and "prompt_text" not in reply["message_context"]


def test_notes_and_alerts_map_to_iam_and_detection_finding_classes(gw):
    a = KEYS["alice"]
    assert (
        gw.post(
            "/me/api/grants",
            json={"agent": "alice-coder", "resource": "github-acme", "scopes": ["read"], "hours": 1},
            headers=a,
        ).status_code
        == 200
    )
    gw.delete("/me/api/grants/alice-coder/github-acme", headers=a)
    gw.post("/admin/risk/bob", json={"level": "watch", "reason": "case 7"})
    gw.post("/admin/resources/github-acme/suspend", json={"suspended": True})
    guard(gw, "-----BEGIN RSA PRIVATE KEY-----\nMIIE", who="alice")  # alert category

    by = {}
    for e in export(gw, "ocsf"):
        by.setdefault((e["class_uid"], e["activity_id"]), []).append(e)
    grant, revoke = by[(3007, 14)][0], by[(3007, 15)][0]
    assert grant["user"] == {"uid": "alice-coder", "name": "alice-coder", "type_id": 99, "type": "AI Agent"}
    assert grant["privileges"] == ["read"] and grant["resources"][0]["uid"] == "github-acme"
    assert grant["actor"]["user"]["uid"] == "alice" and revoke["user"]["uid"] == "alice-coder"
    level = by[(3004, 3)][0]
    assert level["entity"]["uid"] == "bob" and level["entity"]["data"]["reason"] == "case 7"
    assert (level["risk_level"], level["risk_level_id"]) == ("Medium", 2)
    assert level["actor"]["user"] == {"uid": "security", "name": "security", "type_id": 2, "type": "Admin"}
    assert by[(3004, 12)][0]["entity"]["uid"] == "github-acme"

    alert = by[(2004, 1)][0]
    assert alert["type_uid"] == 200401 and alert["is_alert"] is True
    assert alert["finding_info"]["title"] == "Insider risk: alice at normal"
    assert "private_key" in alert["finding_info"]["desc"]
    assert alert["evidences"][0]["actor"]["user"]["uid"] == "alice"
    assert alert["risk_score"] == round(gw.get("/admin/risk").json()["principals"][0]["score"])
    assert (alert["action_id"], alert["disposition_id"]) == (2, 2)


def test_every_record_conforms_to_ocsf_1_9_schema(gw):
    chat_as(gw, KEYS["alice"], f"pay {IBAN}")
    chat_as(gw, AGENT, f"key {AWS}")
    gw.post(
        "/mcp/demo",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_docs", "arguments": {"q": "x"}},
        },
        headers=KEYS["alice"],
    )
    gw.post(
        "/me/api/grants",
        json={"agent": "alice-coder", "resource": "github-acme", "scopes": ["read"], "hours": 1},
        headers=KEYS["alice"],
    )
    gw.post("/admin/risk/bob", json={"level": "restricted"})
    gw.post("/admin/resources/github-acme/suspend", json={"suspended": False})
    docs = export(gw, "ocsf")
    assert {d["class_uid"] for d in docs} == {6003, 2004, 3007, 3004}
    assert any(d.get("resources") for d in docs if d["class_uid"] == 6003)  # the MCP tool
    for d in docs:
        assert conforms(d, SCHEMA["classes"][str(d["class_uid"])]) == [], d


def test_ecs_view(gw):
    chat_as(gw, AGENT, f"key {AWS}")
    docs = export(gw, "ecs")
    decision = next(d for d in docs if d["event"]["kind"] == "event")
    alert = next(d for d in docs if d["event"]["kind"] == "alert")
    assert decision["ecs"]["version"] == "9.5.0"
    assert decision["event"]["category"] == ["api"] and decision["event"]["type"] == ["denied"]
    assert decision["event"]["action"] == "block" and decision["event"]["outcome"] == "failure"
    assert decision["user"] == {"id": "alice", "name": "alice"}  # the person; the agent is gen_ai.agent
    assert decision["gen_ai"]["agent"]["id"] == "alice-coder"
    assert decision["gen_ai"]["request"]["model"] == "mock-model"
    assert decision["rule"]["name"] == "secrets/aws_access_key"
    assert decision["source"]["ip"] == "10.1.2.3"
    assert sorted(decision["related"]["user"]) == ["alice", "alice-coder"]
    assert alert["event"]["category"] == ["intrusion_detection"]
    assert alert["controllayer"]["principal"] == "alice-coder" and alert["user"]["name"] == "alice"


def test_privacy_is_identical_in_every_format(gw):
    """Masked spans, no secrets, raw text only where the native record has it."""
    guard(gw, f"iban {IBAN} then mail {EMAIL} ok")  # redact + a log-only span after a shorter mask
    chat_as(gw, KEYS["alice"], f"key {AWS}")  # block: no text at all
    gw.post("/admin/risk/bob", json={"level": "watch"})
    guard(gw, "watched note", who="bob")  # watch: full text captured
    native = export(gw, "jsonl")
    assert native[0]["text"] == "iban [REDACTED:iban] then mail [REDACTED:email] ok"
    assert [e.get("raw_text") for e in native] == [None, None, "watched note"]
    for fmt in ("jsonl", "ocsf", "ecs"):
        body = gw.get(f"/admin/audit/export?format={fmt}").text
        for secret in (IBAN, EMAIL, AWS, "smith", "0712 1981"):  # not a bare "1981": timestamps and hashes have it
            assert secret not in body, (fmt, secret)
    ocsf = [d for d in export(gw, "ocsf") if d["class_uid"] == 6003]
    assert ocsf[0]["message_context"]["prompt_text"] == native[0]["text"]
    assert "prompt_text" not in ocsf[1]["message_context"]
    assert ocsf[-1]["unmapped"]["raw_text"] == "watched note"
    assert all("raw_text" not in d["unmapped"] for d in ocsf[:-1])
    ecs = [d for d in export(gw, "ecs") if d["event"]["category"] == ["api"]]
    assert [d["controllayer"].get("raw_text") for d in ecs] == [None, None, "watched note"]


def test_unknown_format_is_rejected(gw):
    assert gw.get("/admin/audit/export?format=osfc").status_code == 400


@pytest.mark.parametrize("fmt", ["native", "ocsf", "ecs"])
def test_alert_sink_format(make_client, policy_dir, fmt):
    def sinks(p):
        p["insider_risk"]["sinks"] = [
            {"type": "file", "path": "data/alerts.jsonl", **({} if fmt == "native" else {"format": fmt})}
        ]

    c = make_client(mutate=sinks)
    guard(c, "-----BEGIN RSA PRIVATE KEY-----\nMIIE")
    doc = json.loads((policy_dir / "data/alerts.jsonl").read_text().splitlines()[0])
    if fmt == "native":
        assert doc["principal"] == "alice" and "private_key" in doc["reason"]
    elif fmt == "ocsf":
        assert doc["class_uid"] == 2004 and doc["evidences"][0]["actor"]["user"]["uid"] == "alice"
    else:
        assert doc["event"]["kind"] == "alert" and doc["user"]["name"] == "alice"


def test_bad_sink_format_is_rejected(make_client, policy_dir):
    c = make_client()
    edit_policy(policy_dir, lambda p: p["insider_risk"]["sinks"][0].update(format="splunk"))
    r = c.post("/admin/policy/reload")
    assert r.status_code == 422 and "format" in r.json()["error"]
