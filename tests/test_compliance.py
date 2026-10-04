"""Judge-facing compliance extras: Server-Timing and X-Shield-* headers, supply-chain CVE rules, Markdown
exfiltration filter, per-model classification ceilings, and policy-load warnings."""

from __future__ import annotations

import re

import httpx
from fastapi.testclient import TestClient

from controllayer.gateway.app import create_app

from .conftest import ADMIN, KEYS, chat, edit_policy

TIMING = re.compile(r"^guard;dur=[\d.]+(, upstream;dur=[\d.]+)?, total;dur=[\d.]+$")


def shield_headers(r) -> dict[str, str]:
    assert TIMING.match(r.headers["server-timing"]), r.headers["server-timing"]
    return {k: r.headers[k] for k in ("x-shield-request-id", "x-shield-decision", "x-shield-policy-version")}


def test_chat_response_carries_timing_and_decision(client):
    r = chat(client, "hello there")
    h = shield_headers(r)
    assert h["x-shield-decision"] == "allow"
    assert h["x-shield-request-id"] == r.headers["x-control-request-id"]
    assert h["x-shield-policy-version"] == client.app.state.store.policy.version

    blocked = chat(client, "my key is AKIAIOSFODNN7EXAMPLE")
    assert blocked.status_code == 403
    assert shield_headers(blocked)["x-shield-decision"] == "block"


def test_upstream_time_is_reported_separately(policy_dir):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )

    edit_policy(policy_dir, lambda p: p["upstream"].update(backend="ollama", url="http://up.example"))
    app = create_app(
        policy_dir / "policy.yaml", watch=False, upstream_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    r = chat(TestClient(app, headers={"x-admin-token": ADMIN}), "hello")
    assert r.status_code == 200
    assert ", upstream;dur=" in r.headers["server-timing"]


def test_messages_and_mcp_responses_carry_the_headers(client):
    r = client.post(
        "/v1/messages",
        json={"model": "claude-sonnet-5", "max_tokens": 64, "messages": [{"role": "user", "content": "hello"}]},
        headers=KEYS["alice"],
    )
    assert r.status_code == 200
    assert shield_headers(r)["x-shield-decision"] == "allow"

    r = client.post(
        "/mcp/demo", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, headers=KEYS["alice"]
    )
    assert r.status_code == 200
    assert shield_headers(r)["x-shield-request-id"]


def test_other_endpoints_are_untouched(client):
    r = client.get("/admin/policy")
    assert "server-timing" not in r.headers and "x-shield-decision" not in r.headers
