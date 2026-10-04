"""Identity, model allowlist and tool authorization."""

import pytest

from .conftest import chat, mcp


@pytest.mark.control("model_allowlist")
@pytest.mark.kind("negative")
def test_known_key_and_allowed_model_pass(client):
    assert chat(client, "hello there").status_code == 200


@pytest.mark.control("auth")
@pytest.mark.kind("positive")
def test_missing_key_is_401(client):
    r = chat(client, "hello", who="nobody")
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "policy_violation"


@pytest.mark.control("auth")
@pytest.mark.kind("positive")
def test_forged_key_is_401(client):
    r = client.post(
        "/v1/chat/completions",
        json={"model": "mock-model", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer dev-alice-key-forged"},
    )
    assert r.status_code == 401


@pytest.mark.control("model_allowlist")
@pytest.mark.kind("positive")
def test_model_outside_allowlist_is_refused(client):
    r = chat(client, "hello", model="gpt-5-turbo")
    assert r.status_code == 403
    assert "model_allowlist" in r.json()["error"]["message"]


@pytest.mark.control("model_allowlist")
@pytest.mark.kind("negative")
def test_model_glob_allows_family(client):
    assert chat(client, "hello", model="mock-anything").status_code == 200


@pytest.mark.control("tool_access")
@pytest.mark.kind("negative")
def test_role_may_call_permitted_tool(client):
    r = mcp(client, "tools/call", {"name": "search_docs", "arguments": {"query": "vacation"}})
    assert "result" in r, r


@pytest.mark.control("tool_access")
@pytest.mark.kind("positive")
def test_role_may_not_call_other_tool(client):
    r = mcp(client, "tools/call", {"name": "http_get", "arguments": {"url": "https://acme.example"}}, who="carol")
    assert r["error"]["code"] == -32001
    assert "tool_not_allowed" in r["error"]["message"]


@pytest.mark.control("tool_access")
@pytest.mark.kind("positive")
def test_irreversible_tool_needs_approval_even_for_wildcard_role(client):
    r = mcp(client, "tools/call", {"name": "delete_records", "arguments": {"table": "users"}}, who="ops")
    assert "irreversible_action" in r["error"]["message"]


@pytest.mark.control("auth")
@pytest.mark.kind("positive")
def test_unauthenticated_tools_list_is_refused(client):
    r = mcp(client, "tools/list", who="nobody")
    assert r["error"]["code"] == -32001


def test_unknown_mcp_server(client):
    r = mcp(client, "tools/list", server="nope")
    assert r["error"]["code"] == -32004
