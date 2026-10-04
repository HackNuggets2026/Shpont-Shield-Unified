"""Judge-facing compliance extras: Server-Timing and X-Shield-* headers, supply-chain CVE rules, Markdown
exfiltration filter, per-model classification ceilings, and policy-load warnings."""

from __future__ import annotations

import re

import httpx
import pytest
from fastapi.testclient import TestClient

from controllayer.config import parse_policy
from controllayer.gateway.app import create_app

from .conftest import ADMIN, KEYS, chat, edit_policy, guard, mcp, reply

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


# ---- Markdown image / link exfiltration --------------------------------------------------------------


def test_foreign_markdown_image_target_is_redacted(client):
    v = guard(client, "Summary done. ![chart](https://attacker.example/c.png)", direction="output").json()
    assert v["action"] == "redact"
    assert v["text"] == "Summary done. ![chart]([REDACTED:exfil_url])"
    assert any(f["control"] == "exfiltration" and f["category"] == "markdown_image" for f in v["findings"])


def test_foreign_markdown_link_and_html_image_are_redacted(client):
    text = 'See [the report](http://203.0.113.9/r?q=abc) and <img src="https://x.example/p.gif">'
    v = guard(client, text, direction="tool_result").json()
    assert v["action"] == "redact"
    assert "203.0.113.9" not in v["text"] and "x.example" not in v["text"]
    assert "[the report]" in v["text"]


def test_allowed_and_company_domains_pass(client):
    for text in (
        "![logo](https://acme.example/logo.png)",
        "[docs](https://docs.acme.io/start)",  # company_domains, subdomain
        "[issue](https://github.com/acme/api/issues/12)",
        "plain URL https://attacker.example/x is not rendered",
    ):
        v = guard(client, text, direction="output").json()
        assert not any(f["control"] == "exfiltration" for f in v["findings"]), (text, v)


def test_exfiltration_only_inspects_its_directions(client):
    v = guard(client, "![x](https://attacker.example/x.png)", direction="input").json()
    assert not any(f["control"] == "exfiltration" for f in v["findings"])


def test_exfiltration_block_mode_and_allowlist_edit(make_client):
    def strict(p):
        p["exfiltration"].update(mode="block", allowed_domains=["attacker.example"])

    c = make_client(mutate=strict)
    assert guard(c, "![x](https://attacker.example/x.png)", direction="output").json()["action"] == "allow"
    assert guard(c, "![x](https://other.example/x.png)", direction="output").json()["action"] == "block"


def test_chat_reply_has_the_image_target_cut_out(client, monkeypatch):
    from controllayer.gateway import upstream

    monkeypatch.setitem(upstream.MOCK_REPLIES, "show-chart", "Done: ![chart](https://img.attacker.example/a.png)")
    r = chat(client, "show-chart please")
    assert reply(r) == "Done: ![chart]([REDACTED:exfil_url])"


def test_mcp_tool_result_has_the_image_target_cut_out(client):
    r = mcp(client, "tools/call", {"name": "get_weather", "arguments": {"city": "![map](https://evil.example/m.png)"}}, who="ops")
    assert r["result"]["content"][0]["text"] == "Sunny in ![map]([REDACTED:exfil_url])", r


# ---- per-model classification ceilings ---------------------------------------------------------------

DEAL = "Summarise the due diligence findings before we sign the term sheet with ShopFlow."
MARGIN = "Draft a note on our gross margin per seat after the price increase."


def try_as(client, text, model, principal="alice"):
    return client.post("/admin/try", json={"principal": principal, "text": text, "model": model}).json()


def test_same_text_is_blocked_for_a_cloud_model_and_allowed_on_prem(client):
    cloud = try_as(client, DEAL, "gpt-4o")
    assert cloud["action"] == "block"
    f = next(f for f in cloud["findings"] if f["control"] == "confidentiality")
    assert f["category"] == "mergers_acquisitions" and "accepts up to internal" in f["detail"]

    local = try_as(client, DEAL, "llama3.2:latest")
    assert local["action"] in ("allow", "log")
    f = next(f for f in local["findings"] if f["control"] == "confidentiality")
    assert f["action"] == "log" and f["detail"].startswith("restricted topic")


def test_confidential_text_is_refused_by_an_internal_only_model_through_chat(client):
    assert chat(client, MARGIN, model="gpt-4o").status_code == 403
    assert chat(client, MARGIN, model="qwen3:8b").status_code == 200


def test_unclassified_text_and_models_without_a_ceiling_pass(client):
    v = try_as(client, "What is the CSS margin of the header?", "gpt-4o")
    assert not any(f["control"] == "confidentiality" for f in v["findings"])
    v = try_as(client, DEAL, "mock-model")  # the mock catalog entry sets no max_classification
    assert not any(f["action"] == "block" for f in v["findings"] if f["control"] == "confidentiality")


def test_ceiling_and_topics_come_from_the_policy(make_client):
    def mutate(p):
        p["catalog"]["llama"]["max_classification"] = "public"
        p["confidentiality"]["topics"]["codenames"] = {"classification": "internal", "keywords": ["\\bBluebird\\b"]}

    c = make_client(mutate=mutate)
    assert try_as(c, "Status of Bluebird?", "llama3.2:latest")["action"] == "block"
    assert try_as(c, "Status of bluebirds?", "llama3.2:latest")["action"] == "allow"


def test_bad_classification_values_are_rejected(policy_dir):
    text = (policy_dir / "policy.yaml").read_text()
    with pytest.raises(ValueError):
        parse_policy(text.replace("max_classification: internal", "max_classification: secret", 1))
