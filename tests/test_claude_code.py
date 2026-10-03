"""Claude Code: OTLP telemetry in, hooks enforcing the same policy, detections on what it reports."""

import gzip
import json
from pathlib import Path

import pytest

from .conftest import ADMIN, KEYS

FIX = Path(__file__).parent / "fixtures"
LOGS = json.loads((FIX / "claude_code_logs.json").read_text())
METRICS = json.loads((FIX / "claude_code_metrics.json").read_text())
CENTRAL = {"x-admin-token": ADMIN, "Authorization": ""}  # a collector with no employee key


def events(c, **q):
    return c.app.state.layer.usage.events(source="claude_code", **q)


def hook(c, event, who="alice", headers=None, **body):
    return c.post(
        "/v1/hooks/claude-code",
        json={"hook_event_name": event, "session_id": "sess-1", "cwd": "/repo", "permission_mode": "default"} | body,
        headers={**KEYS.get(who, {}), **(headers or {})},
    ).json()


def test_otlp_logs_meter_cost_and_join_by_email(client):
    assert client.post("/v1/logs", json=LOGS, headers=CENTRAL).status_code == 200
    ev = {e["kind"]: e for e in events(client)}
    assert {"cc.user_prompt", "cc.api_request", "cc.tool_decision", "cc.tool_result"} <= set(ev)
    api = ev["cc.api_request"]
    assert (api["principal"], api["team"], api["resource"]) == ("alice", "engineering", "claude_code")
    assert (api["usd"], api["tokens"], api["workflow"], api["task"]) == (0.0123, 1500, "bugfix", "BUG-77")
    assert api["client"].startswith("claude-code/2.1.0")
    assert ev["cc.tool_decision"]["decision"] == "reject" and ev["cc.tool_decision"]["tool"] == "Edit"
    # Prompt text never lands in the event stream, even when Claude Code sends it.
    assert "AKIA" not in json.dumps(ev["cc.user_prompt"]) and ev["cc.user_prompt"]["detail"] == {"prompt_length": 42}
    rows = client.get("/admin/usage", params={"by": "resource,source", "days": 400}).json()
    row = next(r for r in rows if r["resource"] == "claude_code")
    assert row["source"] == "claude_code" and row["usd"] == pytest.approx(0.0123)


def test_otlp_with_an_employee_key_is_that_employee(client):
    raw = gzip.compress(json.dumps(LOGS).encode())
    r = client.post("/v1/logs", content=raw, headers={**KEYS["carol"], "content-encoding": "gzip",
                                                       "content-type": "application/json"})  # fmt: skip
    assert r.status_code == 200
    assert {e["principal"] for e in events(client)} == {"carol"}
    assert client.post("/v1/logs", json=LOGS, headers={"x-admin-token": "no"}).status_code == 401
    r = client.post(
        "/v1/logs", content=b"\x0a\x01", headers={**KEYS["carol"], "content-type": "application/x-protobuf"}
    )
    assert r.status_code == 415


def test_otlp_metrics_keep_value_signals_not_duplicate_cost(client):
    assert client.post("/v1/metrics", json=METRICS, headers=CENTRAL).status_code == 200
    ev = events(client)
    kinds = sorted((e["kind"], e["decision"], e["detail"]["value"]) for e in ev)
    assert ("metric.lines_of_code", "added", 120) in kinds and ("metric.lines_of_code", "removed", 30) in kinds
    assert ("metric.commit", None, 1) in kinds
    assert not any(e["kind"].startswith("metric.cost") for e in ev)  # api_request already carries the money
    # A cumulative series counts only its increase on the next export.
    METRICS2 = json.loads(json.dumps(METRICS))
    METRICS2["resourceMetrics"][0]["scopeMetrics"][0]["metrics"] = [
        m for m in METRICS2["resourceMetrics"][0]["scopeMetrics"][0]["metrics"] if "active_time" in m["name"]
    ]
    METRICS2["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["sum"]["dataPoints"][0]["asDouble"] = 80
    client.post("/v1/metrics", json=METRICS2, headers=CENTRAL)
    active = [e["detail"]["value"] for e in events(client, kind="metric.active_time")]
    assert sorted(active) == [30, 50]


def test_hook_blocks_a_pasted_secret_and_allows_a_normal_prompt(client):
    assert hook(client, "UserPromptSubmit", prompt="explain the retry policy") == {}
    r = hook(client, "UserPromptSubmit", prompt_text="deploy with AKIAIOSFODNN7EXAMPLE please")
    assert r["decision"] == "block" and "Shpont Shield" in r["reason"] and "secrets" in r["reason"]
    ev = client.app.state.layer.usage.events(source="claude_code", kind="check.input")
    assert {e["decision"] for e in ev} >= {"allow"} and any(e["decision"] in ("block", "redact") for e in ev)


def test_hook_applies_role_workflow_and_grant_rules_to_tools(client):
    allow = hook(client, "PreToolUse", tool_name="Edit", tool_input={"file_path": "a.py", "old_string": "x"})
    assert allow == {}
    deny = hook(client, "PreToolUse", tool_name="Edit", tool_input={"file_path": "a.py"},
                headers={"x-acl-workflow": "pr_review"})  # fmt: skip
    out = deny["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "tool_not_in_workflow" in out["permissionDecisionReason"]
    intern = hook(client, "PreToolUse", who="carol", tool_name="Bash", tool_input={"command": "ls"})
    assert "tool_not_allowed" in intern["hookSpecificOutput"]["permissionDecisionReason"]
    # (sess-1 now carries pr_review, which a later call in that session inherits; use another session)
    prod = hook(
        client, "PreToolUse", tool_name="mcp__demo__query_prod_db", tool_input={"sql": "select 1"}, session_id="s2"
    )
    assert "grant_required" in prod["hookSpecificOutput"]["permissionDecisionReason"]
    secret = hook(client, "PreToolUse", tool_name="Bash", tool_input={"command": "export K=AKIAIOSFODNN7EXAMPLE"})
    assert secret["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_without_a_key_blocks_rather_than_letting_work_through_unseen(client):
    r = hook(client, "UserPromptSubmit", who="nobody", headers={"Authorization": ""}, prompt="hi")
    assert r["decision"] == "block" and "SHIELD_KEY" in r["reason"]


def test_post_tool_use_leases_an_argent_simulator(client):
    hook(client, "PostToolUse", tool_name="mcp__argent__boot-device", tool_input={},
         tool_response={"content": [{"type": "text", "text": "booted sim-a1b2c3"}]})  # fmt: skip
    leases = client.get("/admin/leases").json()
    rows = leases["open"] if isinstance(leases, dict) else leases
    assert any(x["resource"] == "simulator" and x["handle"] == "sim-a1b2c3" for x in rows)


def test_bypass_mode_and_unapproved_mcp_server_raise_incidents(client):
    hook(client, "UserPromptSubmit", prompt="go", permission_mode="bypassPermissions")
    hook(client, "PreToolUse", tool_name="mcp__pastebin__upload", tool_input={"text": "x"})
    hook(client, "PreToolUse", tool_name="mcp__demo__search_docs", tool_input={"query": "x"})  # approved
    rules = [i["rule"] for i in client.get("/admin/incidents").json()["incidents"]]
    assert "permission_bypass" in rules and rules.count("unapproved_mcp_server") == 1


def test_rejected_edit_storm(client):
    storm = json.loads(json.dumps(LOGS))
    rec = storm["resourceLogs"][0]["scopeLogs"][0]["logRecords"][2]
    storm["resourceLogs"][0]["scopeLogs"][0]["logRecords"] = [rec] * 5
    client.post("/v1/logs", json=storm, headers=KEYS["alice"])
    assert "rejected_edit_storm" in [i["rule"] for i in client.get("/admin/incidents").json()["incidents"]]
