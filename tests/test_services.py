"""The company services behind /mcp/company: per-tool scopes, filtered tool lists, mock backends."""

import json
import re
import time

import pytest
import yaml

from controllayer import services
from controllayer.config import parse_policy

from .conftest import ROOT

ALICE = {"x-api-key": "dev-alice-key"}
BOB = {"x-api-key": "fin-bob-key"}
CODER = {"x-api-key": "alice-agent-key"}  # alice's agent, engineering
BOB_AGENT = {"x-api-key": "bob-agent-key"}  # bob's agent, finance


def rpc(client, method, params=None, headers=CODER):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post("/mcp/company", headers=headers, json=body).json()


def call(client, tool, args, headers=CODER):
    return rpc(client, "tools/call", {"name": tool, "arguments": args}, headers)


def text(r):
    return r["result"]["content"][0]["text"]


def tool_names(client, headers=CODER):
    return {t["name"] for t in rpc(client, "tools/list", headers=headers)["result"]["tools"]}


def grant(client, resource, scopes=("read",), hours=2, agent="alice-coder", who=ALICE):
    r = client.post(
        "/me/api/grants",
        headers=who,
        json={"agent": agent, "resource": resource, "scopes": list(scopes), "hours": hours},
    )
    assert r.status_code == 200, r.text


def test_catalog_offers_twenty_services_with_scoped_tools():
    policy = parse_policy((ROOT / "policy.yaml").read_text())
    offered = {r.connection["service"] for r in policy.resources.values() if r.type == "service"}
    assert offered == set(services.SERVICES) and len(offered) == 20
    names = [t.name for s in services.SERVICES.values() for t in s.tools]
    assert len(names) == len(set(names)) == 55
    for key, svc in services.SERVICES.items():
        assert 2 <= len(svc.tools) <= 4, key
        assert svc.scopes <= {"read", "write", "admin", "exec", "pii", "external_share"}, key
    assert {k for k, s in services.SERVICES.items() if "pii" in s.scopes} == {"postgres", "snowflake"}
    assert {k for k, s in services.SERVICES.items() if "external_share" in s.scopes} == {
        "gdrive",
        "sendgrid",
        "zendesk",
    }
    egress = {t.name for t in services.TOOLS.values() if t.egress}
    assert egress == {
        "gdrive_share_file",
        "sendgrid_send_email",
        "slack_post_message",
        "zapier_trigger_zap",
        "zendesk_reply",
    }


def test_tools_list_follows_the_live_grant(client):
    assert tool_names(client) == {"list_resources"}
    grant(client, "github-acme")
    assert tool_names(client) == {"list_resources", "github_list_issues", "github_get_file"}
    client.patch("/me/api/grants/alice-coder/github-acme", headers=ALICE, json={"scopes": ["read", "write"]})
    # admin is not in the catalog's scopes for GitHub, so merging is never offered to an agent
    assert tool_names(client) == {"list_resources", "github_list_issues", "github_get_file", "github_create_issue"}
    client.app.state.layer.state.grants["alice-coder"]["github-acme"]["expires_at"] = time.time() - 1
    assert tool_names(client) == {"list_resources"}


def test_list_resources_names_the_tools_each_grant_unlocks(client):
    grant(client, "heroku", scopes=("read", "exec"))
    grant(client, "demo-tools", scopes=("use",))
    lines = text(call(client, "list_resources", {})).splitlines()
    assert lines == [
        "demo-tools: Shared-drive tools (demo MCP server) scopes=['use'] (MCP server at /mcp/demo)",
        "heroku: Heroku (acme-api, acme-web) scopes=['exec', 'read'] "
        "tools=['heroku_list_apps', 'heroku_get_logs', 'heroku_restart_dyno']",
    ]


def test_tools_list_drops_a_suspended_service(client):
    grant(client, "slack")
    assert "slack_read_channel" in tool_names(client)
    client.post("/admin/resources/slack/suspend", json={"suspended": True})
    assert tool_names(client) == {"list_resources"}


def test_employee_sees_tools_of_every_entitled_service(client):
    mine = tool_names(client, ALICE)
    assert {"postgres_query", "heroku_scale_formation", "zendesk_reply"} <= mine
    assert not mine & {"stripe_refund", "salesforce_get_account", "github_merge_pull_request", "zapier_trigger_zap"}
    assert "stripe_refund" in tool_names(client, BOB)


@pytest.mark.parametrize(
    "tool,args,category",
    [
        ("github_create_issue", {"repo": "acme/web", "title": "x"}, "scope_denied"),  # write on a read grant
        ("postgres_query", {"sql": "select 1"}, "not_granted"),  # another service
        ("stripe_refund", {"charge": "ch_3Pq04"}, "not_granted"),  # owner not entitled
    ],
)
def test_each_call_is_authorised_by_the_tools_scope(client, tool, args, category):
    grant(client, "github-acme")
    r = call(client, tool, args)
    assert f"resource_access/{category}" in r["error"]["message"]


def test_exec_and_admin_tools_need_their_own_scope(client):
    grant(client, "heroku", scopes=("read", "exec"))
    restart = call(client, "heroku_restart_dyno", {"app": "acme-api", "dyno": "web.1"})
    assert json.loads(text(restart)) == {"app": "acme-api", "restarted": "web.1"}
    scale = call(client, "heroku_scale_formation", {"app": "acme-api", "process_type": "web", "quantity": 8})
    assert "scope_denied" in scale["error"]["message"]


def test_bad_arguments_get_a_service_error_not_a_crash(client):
    grant(client, "github-acme")
    r = call(client, "github_list_issues", {"repo": "acme/web", "state": "open", "limt": 5})
    assert r["result"]["isError"] and "unknown argument(s) ['limt']" in text(r)
    r = call(client, "github_list_issues", {"repo": "acme/secret"})
    assert r["result"]["isError"] and "'repo' must be one of" in text(r)


@pytest.mark.parametrize(
    "sql,found",
    [
        ("DELETE FROM customers", "DELETE"),
        ("update customers set plan = 'free'", "UPDATE"),
        ("DROP TABLE orders", "DROP"),
        ("WITH gone AS (DELETE FROM orders RETURNING *) SELECT * FROM gone", "DELETE"),
        ("SELECT * INTO backup FROM customers", "INTO"),
        ("CREATE TABLE x (id int)", "CREATE"),
        ("select 1; drop table customers", None),
    ],
)
def test_postgres_replica_rejects_writes_and_ddl(client, sql, found):
    grant(client, "postgres-prod")
    r = call(client, "postgres_query", {"sql": sql})
    assert r["result"]["isError"]
    assert "read-only" in text(r) and (found is None or f"found {found})" in text(r))


def test_read_only_guard_reads_keywords_only_outside_literals(client):
    grant(client, "postgres-prod")
    r = call(client, "postgres_query", {"sql": "select id from orders where status <> 'delete me' -- drop\n"})
    assert json.loads(text(r)) | {"rows": None} == {"columns": ["id"], "rows": None, "row_count": 6, "truncated": False}


def test_runaway_query_is_cancelled(client, monkeypatch):
    monkeypatch.setattr(services, "_QUERY_SECONDS", 0.2)
    grant(client, "postgres-prod")
    sql = "with recursive c(x) as (select 1 union all select x + 1 from c) select count(*) from c"
    r = call(client, "postgres_query", {"sql": sql})
    assert "statement timeout" in text(r)


@pytest.mark.parametrize(
    "sql,error",
    [
        ("select length(randomblob(500000000))", "string or blob too big"),
        ("select a.id from orders a, orders b, orders c", None),  # 216 rows, cut to the limit
        ("select hex(randomblob(40000)) from orders a, orders b", None),  # 36 x 80 kB, cut by size
    ],
)
def test_query_size_is_bounded(client, sql, error):
    grant(client, "postgres-prod")
    r = call(client, "postgres_query", {"sql": sql, "limit": 50})
    if error:
        assert r["result"]["isError"] and error in text(r)
        return
    out = json.loads(text(r))
    assert out["truncated"] is True and 0 < out["row_count"] <= 50 and len(text(r)) < 1_100_000


OPS = {"x-api-key": "ops-agent-key"}  # platform, entitled to the pii scope of postgres-prod


@pytest.mark.parametrize(
    "tool,sql,rows",
    [
        (
            "postgres_query",
            "select name, national_id from customers where id in (1, 2)",
            [{"name": "Jan Kowalski", "national_id": "****"}, {"name": "Maria Garcia", "national_id": None}],
        ),
        ("postgres_query", "select hex(national_id) as v from customers where id = 1", [{"v": "2A2A2A2A"}]),
        (
            "postgres_query",
            "select substr(national_id, 1, 5) || '-' || substr(national_id, 6) as v from customers where id = 3",
            [{"v": "****-"}],
        ),
        (
            "postgres_query",
            "select email, phone from customers where id = 1",
            [{"email": "****@example.com", "phone": "****"}],
        ),
        (
            "snowflake_query",
            "select employee, work_email, base_pay_pln, payout_iban from compensation where level = 'L3'",
            [{"employee": "Bob Lis", "work_email": "****@acme.io", "base_pay_pln": None, "payout_iban": "****"}],
        ),
    ],
)
def test_without_the_pii_scope_queries_run_on_masked_columns(client, tool, sql, rows):
    grant(client, "postgres-prod")
    grant(client, "snowflake")
    assert json.loads(text(call(client, tool, {"sql": sql})))["rows"] == rows


def test_the_pii_scope_needs_its_own_entitlement(client):
    r = client.post(
        "/me/api/grants",
        headers=ALICE,
        json={"agent": "alice-coder", "resource": "postgres-prod", "scopes": ["read", "pii"], "hours": 1},
    )
    assert r.status_code == 400 and "subset of ['read']" in r.text
    out = json.loads(text(call(client, "postgres_query", {"sql": "select national_id from customers"}, ALICE)))
    assert {row["national_id"] for row in out["rows"]} == {"****", None}
    out = json.loads(text(call(client, "postgres_query", {"sql": "select national_id from customers"}, OPS)))
    assert [row["national_id"] for row in out["rows"]] == ["44051401359", None, "078-05-1120", "02070803628", None]


def test_with_the_pii_scope_content_checks_still_redact(make_client):
    client = make_client(
        mutate=lambda p: p["resources"]["postgres-prod"]["scope_entitlements"]["pii"].update(teams=["engineering"])
    )
    grant(client, "postgres-prod", scopes=("read", "pii"))
    r = call(client, "postgres_query", {"sql": "select name, national_id from customers where national_id is not null"})
    rows = json.loads(text(r))["rows"]
    assert [row["national_id"] for row in rows] == ["[REDACTED:pesel]", "[REDACTED:us_ssn]", "[REDACTED:pesel]"]


def test_an_email_without_a_domain_is_masked_whole():
    table = {
        "columns": "a TEXT, b TEXT",
        "rows": [["jan@acme.io", "jan.kowalski"]],
        "masked": {"a": "email", "b": "email"},
    }
    assert services._masked({"t": table})["t"]["rows"] == [["****@acme.io", "****"]]


def test_masking_rules_name_real_columns_and_known_classes():
    table = {"columns": "id INTEGER, ssn TEXT", "rows": [[1, "078-05-1120"]]}
    with pytest.raises(ValueError, match=r"t.masked: unknown column or class \{'ssn': 'us_ssn'\}"):
        services._masked({"t": table | {"masked": {"ssn": "us_ssn"}}})
    with pytest.raises(ValueError, match=r"unknown column or class \{'tax_id': 'national_id'\}"):
        services._masked({"t": table | {"masked": {"tax_id": "national_id"}}})


def test_card_pasted_into_a_ticket_is_redacted_for_engineering_and_blocked_for_finance(client):
    grant(client, "zendesk")
    ticket = json.loads(text(call(client, "zendesk_get_ticket", {"ticket_id": 4521})))
    assert "The card is [REDACTED:credit_card]," in ticket["description"]
    grant(client, "zendesk", agent="bob-assistant", who=BOB)
    r = call(client, "zendesk_get_ticket", {"ticket_id": 4521}, BOB_AGENT)
    assert r["error"]["message"].startswith("blocked by policy: pii/credit_card") and "4111" not in json.dumps(r)


@pytest.mark.parametrize(
    "resource,tool,args",
    [
        ("zendesk", "zendesk_get_ticket", {"ticket_id": 4533}),
        ("notion", "notion_get_page", {"page_id": "c3d4e5f6"}),
    ],
)
def test_planted_instructions_in_a_document_are_blocked(client, resource, tool, args):
    grant(client, resource)
    r = call(client, tool, args)
    assert r["error"]["message"].startswith("blocked by policy: prompt_injection")
    assert "partner-tools" not in json.dumps(r)


@pytest.mark.parametrize(
    "resource,tool,args,secret",
    [
        ("aws-s3", "s3_get_object", {"bucket": "acme-deploy-artifacts", "key": "api/.env.production"}, "AKIA"),
        ("github-acme", "github_get_file", {"repo": "acme/infra", "path": "scripts/bootstrap.sh"}, "ghp_"),
    ],
)
def test_a_key_leaked_into_company_data_is_blocked(client, resource, tool, args, secret):
    grant(client, resource)
    r = call(client, tool, args)
    assert r["error"]["message"].startswith("blocked by policy: secrets/") and secret not in json.dumps(r)


def test_credentials_are_injected_but_never_returned_or_audited(client, policy_dir, monkeypatch):
    policy = parse_policy((ROOT / "policy.yaml").read_text())
    envs = {rid: r.connection["secret_env"] for rid, r in policy.resources.items() if r.type == "service"}
    secrets = {rid: f"live-{rid}-Zq81xW" for rid in envs}
    for rid, env in envs.items():
        monkeypatch.setenv(env, secrets[rid])
    seen = []
    real = services.call

    def spy(svc, tool, args, headers, scopes):
        seen.append(headers)
        return real(svc, tool, args, headers, scopes)

    monkeypatch.setattr(services, "call", spy)

    grant(client, "github-acme")
    grant(client, "slack")
    out = [
        rpc(client, "tools/list"),
        call(client, "list_resources", {}),
        call(client, "github_list_issues", {"repo": "acme/web"}),
        call(client, "slack_read_channel", {"channel": "incidents"}),
        rpc(client, "tools/list", headers=ALICE),
        call(client, "datadog_search_logs", {"query": "acme"}, ALICE),
    ]
    assert seen == [
        {"Authorization": f"Bearer {secrets['github-acme']}"},
        {"Authorization": f"Bearer {secrets['slack']}"},
        {"DD-API-KEY": secrets["datadog"]},
    ]
    exposed = json.dumps(out) + (policy_dir / "data" / "audit.jsonl").read_text()
    exposed += json.dumps(list(client.app.state.layer.audit.events)) + client.get("/admin/grants").text
    assert "Login button" in exposed  # the results themselves did come back
    for rid in envs:
        assert secrets[rid] not in exposed and envs[rid] not in exposed


@pytest.mark.parametrize(
    "change,error",
    [
        (lambda r: r["slack"]["connection"].update(service="slak"), "unknown service 'slak'"),
        (lambda r: r["slack"].update(scopes=["read", "exec"]), "scopes ['exec'] unlock no slack tool"),
        (lambda r: r["slack"].update(scopes=["read", "pii"]), "scopes ['pii'] unlock no slack tool"),
        (lambda r: r["slack"]["connection"].pop("secret_env"), "connection is {service"),
        (lambda r: r["notion"]["connection"].update(service="slack"), "both offer service 'slack'"),
    ],
)
def test_catalog_rejects_unknown_services_and_unusable_scopes(change, error):
    data = yaml.safe_load((ROOT / "policy.yaml").read_text())
    change(data["resources"])
    with pytest.raises(ValueError, match=re.escape(error)):
        parse_policy(yaml.safe_dump(data))


@pytest.mark.parametrize(
    "tool,sql,value",
    [
        ("postgres_query", "select x'41' as v", "\\x41"),
        ("snowflake_query", "select zeroblob(2) as v", "\\x0000"),
        ("postgres_query", "select -1e999 as v", "-Infinity"),
    ],
)
def test_blobs_and_overflowed_floats_come_back_as_postgres_prints_them(client, tool, sql, value):
    grant(client, "postgres-prod")
    grant(client, "snowflake")
    assert json.loads(text(call(client, tool, {"sql": sql})))["rows"] == [{"v": value}]


def test_random_blob_is_hex_encoded(client):
    grant(client, "postgres-prod")
    (row,) = json.loads(text(call(client, "postgres_query", {"sql": "select randomblob(4) as v"})))["rows"]
    assert re.fullmatch(r"\\x[0-9a-f]{8}", row["v"])


def test_an_answer_that_is_not_json_is_a_tool_error_not_a_crash(client, monkeypatch):
    monkeypatch.setattr(services, "call", lambda *a, **kw: {"rows": [object(), float("nan")]})
    grant(client, "postgres-prod")
    r = call(client, "postgres_query", {"sql": "select 1"})
    assert r["result"]["isError"] and text(r).startswith("PostgreSQL (prod read replica): the answer cannot be sent")


@pytest.mark.parametrize(
    "sql",
    [
        "select [a'b] from customers; delete from customers where x = '1'",
        "select `a'b` from customers; delete from customers where x = '1'",
        "select \"a'b\" from customers; delete from customers where x = '1'",
    ],
)
def test_a_quote_inside_a_quoted_name_does_not_hide_a_second_statement(sql):
    with pytest.raises(services.ServiceError, match="send exactly one statement"):
        services.read_only_sql(sql, "pg")


def test_keywords_in_quoted_names_are_names(client):
    grant(client, "postgres-prod")
    r = call(client, "postgres_query", {"sql": 'select 1 as [delete], 2 as `drop`, 3 as "into"'})
    assert json.loads(text(r))["rows"] == [{"delete": 1, "drop": 2, "into": 3}]


def test_a_redacted_value_keeps_the_answer_valid_json(client):
    grant(client, "heroku")
    lines = json.loads(text(call(client, "heroku_get_logs", {"app": "acme-api"})))
    assert len(lines) == 4 and lines[3].endswith("status=200 service=41ms")
    assert lines[2] == "2026-10-03T07:58:09Z app[worker.1]: connecting to [REDACTED:credentials_in_url]"


CAROL = {"x-api-key": "intern-key"}
HANDBOOK, SALARIES = "1hb7Kq", "1sb3Zx"


def share(client, file_id, email, headers=ALICE):
    return call(client, "gdrive_share_file", {"file_id": file_id, "email": email, "role": "reader"}, headers)


@pytest.mark.parametrize(
    "email,inside",
    [
        ("bob@acme.io", True),
        ("Bob@ACME.IO", True),
        ("bob@eu.acme.io", True),
        ("me@gmail.com", False),
        ("bob@acme.io.evil.com", False),
        ("bob@notacme.io", False),
        ("bob@acme.io, me@gmail.com", False),
        ("Bob <bob@acme.io>", False),
        ("bob@acme.io@gmail.com", False),
        ("acme.io", False),
    ],
)
def test_sharing_outside_the_company_domains_needs_external_share(client, email, inside):
    r = share(client, HANDBOOK, email)
    if inside:
        assert json.loads(text(r))["file_id"] == HANDBOOK
    else:
        assert r["error"]["message"] == (
            f"blocked by policy: resource_access/external_recipient: [{email!r}] is outside the company domains "
            "['acme.io']; that needs external_share"
        )


def test_an_entitled_scope_lets_a_harmless_file_out_but_never_a_confidential_one(client):
    assert json.loads(text(share(client, HANDBOOK, "me@gmail.com", BOB)))["file_id"] == HANDBOOK
    for who, email in ((BOB, "me@gmail.com"), (ALICE, "hr@acme.io")):
        r = share(client, SALARIES, email, who)
        assert r["error"]["message"] == (
            "blocked by policy: confidential_output/confidential_output (in what gdrive_share_file would send)"
        )
        assert "180k" not in json.dumps(r)


def test_a_shared_file_is_vetted_by_its_text_not_its_owner_metadata(client):
    client.post("/admin/risk/alice", json={"level": "watch"})  # watched: emails are redacted too
    assert json.loads(text(share(client, HANDBOOK, "bob@acme.io")))["file_id"] == HANDBOOK


def test_an_agent_shares_outside_only_with_external_share_in_its_grant(client):
    grant(client, "google-drive", scopes=("read", "admin"))
    assert "external_recipient" in share(client, HANDBOOK, "me@gmail.com", CODER)["error"]["message"]
    grant(client, "google-drive", scopes=("read", "admin", "external_share"), agent="bob-assistant", who=BOB)
    assert json.loads(text(share(client, HANDBOOK, "me@gmail.com", BOB_AGENT)))["file_id"] == HANDBOOK
    events = [e for e in client.app.state.layer.audit.events if e.get("principal") == "alice-coder"]
    assert any(f["category"] == "external_recipient" for e in events for f in e["findings"])


def test_a_slack_post_is_checked_like_a_tool_result(client):
    grant(client, "slack", scopes=("read", "write"))
    r = call(client, "slack_post_message", {"channel": "general", "text": "Engineering L3 salary: 180k-210k PLN"})
    assert r["error"]["message"].startswith("blocked by policy: confidential_output/")
    ok = call(client, "slack_post_message", {"channel": "general", "text": "Deploy of acme-api finished"})
    assert json.loads(text(ok))["ok"] is True


def test_email_to_a_customer_needs_external_share_and_a_clean_message(client):
    send = {"to": "jan.kowalski@example.com", "template_id": "d-91b2e4", "dynamic_data": {"amount": "2,499 PLN"}}
    grant(client, "sendgrid", scopes=("read", "write"), agent="bob-assistant", who=BOB)
    assert "external_recipient" in call(client, "sendgrid_send_email", send, BOB_AGENT)["error"]["message"]
    client.patch("/me/api/grants/bob-assistant/sendgrid", headers=BOB, json={"scopes": ["write", "external_share"]})
    assert json.loads(text(call(client, "sendgrid_send_email", send, BOB_AGENT)))["status"] == 202
    leak = send | {"dynamic_data": {"note": "unreleased pricing, internal only"}}
    r = call(client, "sendgrid_send_email", leak, BOB_AGENT)
    assert r["error"]["message"].startswith("blocked by policy: confidential_output/")


def test_a_public_ticket_reply_is_email_to_the_customer(client):
    grant(client, "zendesk", scopes=("read", "write"))
    reply = {"ticket_id": 4521, "body": "Refund issued."}
    assert "external_recipient" in call(client, "zendesk_reply", reply)["error"]["message"]
    assert json.loads(text(call(client, "zendesk_reply", reply | {"public": False})))["public"] is False
    r = call(client, "zendesk_reply", {"ticket_id": 999, "body": "Hi"})
    assert r["result"]["isError"] and text(r) == "Zendesk: 404 no ticket 999"


def test_a_zap_payload_is_checked_before_the_zap_runs(client):
    r = call(client, "zapier_trigger_zap", {"zap_id": "zap_303", "payload": {"rows": "Q4 salary bands"}}, BOB)
    assert r["error"]["message"].startswith("blocked by policy: confidential_output/")
    assert json.loads(text(call(client, "zapier_trigger_zap", {"zap_id": "zap_303"}, BOB)))["status"] == "success"


def test_sharing_an_unknown_file_is_a_service_error(client):
    r = share(client, "nope", "bob@acme.io")
    assert r["result"]["isError"] and text(r) == "Google Drive: 404 no file 'nope'"
