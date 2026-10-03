"""Budget and resource governance: rate limits, token/cost budgets for commercial and local models, loop guard."""

from .conftest import chat


def _limits(**kw):
    def mutate(p):
        p["budgets"]["per_team"]["engineering"] = kw

    return mutate


def test_rate_limit_per_team(make_client):
    c = make_client(mutate=_limits(requests_per_minute=3))
    codes = [chat(c, f"question {i}").status_code for i in range(5)]
    assert codes == [200, 200, 200, 429, 429]


def test_token_budget_blocks_once_spent(make_client):
    c = make_client(mutate=_limits(tokens_per_day=60))
    assert chat(c, "x" * 100).status_code == 200  # ~25 in + reply tokens recorded
    r = chat(c, "y" * 100)
    assert r.status_code == 429
    assert "token_budget" in r.json()["error"]["message"]


def test_cost_budget_commercial_pricing(make_client):
    # mock-model is priced at $1/1M input + $2/1M output tokens
    c = make_client(mutate=_limits(usd_per_day=0.0001))
    assert chat(c, "a" * 400).status_code == 200
    r = chat(c, "b" * 400)
    assert r.status_code == 429
    assert "cost_budget" in r.json()["error"]["message"]


def test_local_model_cost_uses_compute_seconds(make_client):
    c = make_client(mutate=lambda p: p["budgets"]["pricing"].update({"mock-local": {"usd_per_compute_second": 1000.0}}) or p["models"]["allowed"].append("mock-local"))
    assert chat(c, "hello", model="mock-local").status_code == 200
    models = c.get("/admin/summary").json()["budgets"]["models"]
    row = next(m for m in models if m["model"] == "mock-local")
    assert row["usd"] > 0 and row["compute_seconds"] > 0


def test_usage_is_attributed_to_principal_team_and_global(client):
    chat(client, "hello world")
    scopes = {(s["scope"], s["key"]): s for s in client.get("/admin/summary").json()["budgets"]["scopes"]}
    assert scopes[("principal", "alice")]["requests"] == 1
    assert scopes[("team", "engineering")]["requests"] == 1
    assert scopes[("global", "*")]["requests"] == 1
    assert scopes[("team", "interns")]["requests"] == 0


def test_runaway_loop_is_cut(client):
    codes = [chat(client, "same prompt again").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5
    assert codes[5:] == [429, 429]


def test_shadow_budget_reports_but_allows(make_client):
    def mutate(p):
        p["budgets"]["shadow"] = True
        p["budgets"]["per_team"]["engineering"] = {"requests_per_minute": 1}

    c = make_client(mutate=mutate)
    assert [chat(c, f"q{i}").status_code for i in range(3)] == [200, 200, 200]
    shadow = c.get("/admin/summary").json()["shadow_would_have"]
    assert any(k.startswith("budget/rate_limit") for k, _ in shadow)


def test_mcp_tool_calls_are_rate_limited(make_client):
    from .conftest import mcp

    c = make_client(mutate=_limits(requests_per_minute=2))
    codes = [("error" in mcp(c, "tools/call", {"name": "search_docs", "arguments": {"query": f"q{i}"}})) for i in range(3)]
    assert codes == [False, False, True]


def test_cost_limit_is_inclusive(make_client):
    c = make_client(mutate=_limits(usd_per_day=0.0))
    assert chat(c, "free?").status_code == 429


def test_tool_only_turns_are_not_mistaken_for_a_loop(client):
    from .conftest import KEYS

    codes = []
    for i in range(7):
        r = client.post("/v1/chat/completions", headers=KEYS["alice"], json={"model": "mock-model", "messages": [
            {"role": "user", "content": "run the tool"}, {"role": "assistant", "content": "calling"},
            {"role": "tool", "content": f"result number {i}"}]})
        codes.append(r.status_code)
    assert codes == [200] * 7


def test_tool_only_turn_still_gated(client):
    r = client.post("/v1/chat/completions", headers={}, json={"model": "mock-model", "messages": [
        {"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}, {"role": "tool", "content": "z"}]})
    assert r.status_code == 401
