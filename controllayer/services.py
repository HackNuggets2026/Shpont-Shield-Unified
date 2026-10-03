"""The company's SaaS and data services, as the `company` MCP server offers them to agents and staff.

Each service is a catalog resource in the policy (`type: service`, `connection.service: <key>`).
Every tool declares the grant scope it needs (read, write, admin or exec), so a grant maps
directly to the tools it unlocks. The backends are deterministic in-process mocks of the real
APIs over the records in services.yaml. Writes are answered the way the real API would answer,
but nothing is persisted.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

DATA: dict[str, Any] = yaml.safe_load(Path(__file__).with_name("services.yaml").read_text())


class ServiceError(Exception):
    """An error answer from a service, returned to the caller as a failed tool call."""

    def __init__(self, status: int, message: str):
        super().__init__(f"{status} {message}")


@dataclass(frozen=True)
class Tool:
    name: str
    scope: str
    description: str
    params: dict[str, dict[str, Any]]
    run: Callable[[dict[str, Any]], Any]
    required: tuple[str, ...] = ()

    def spec(self) -> dict[str, Any]:
        schema = {"type": "object", "properties": self.params, "required": list(self.required)}
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": schema | {"additionalProperties": False},
        }


@dataclass(frozen=True)
class Service:
    title: str
    tools: tuple[Tool, ...]
    auth_header: str = "Authorization"
    auth_format: str = "Bearer {secret}"

    @cached_property
    def scopes(self) -> set[str]:
        return {t.scope for t in self.tools}


def call(service: Service, tool: Tool, args: dict[str, Any], headers: dict[str, str]) -> Any:
    """One API call, authenticated with `headers` the way the service's real client would be."""
    if not headers.get(service.auth_header):
        raise ServiceError(401, f"Unauthorized: missing {service.auth_header}")
    _validate(tool, args)
    defaults = {k: p["default"] for k, p in tool.params.items() if "default" in p}
    return tool.run(defaults | args)


# ---- argument schemas ---------------------------------------------------------------------------

_PY: dict[str, type] = {"string": str, "integer": int, "boolean": bool, "object": dict, "array": list}


def _validate(tool: Tool, args: dict[str, Any]) -> None:
    unknown = sorted(set(args) - set(tool.params))
    if unknown:
        raise ServiceError(400, f"{tool.name}: unknown argument(s) {unknown}; expected {sorted(tool.params)}")
    missing = [k for k in tool.required if k not in args]
    if missing:
        raise ServiceError(400, f"{tool.name}: missing required argument(s) {missing}")
    for k, v in args.items():
        p = tool.params[k]
        t = _PY[p["type"]]
        if not isinstance(v, t) or (t is int and isinstance(v, bool)):
            raise ServiceError(400, f"{tool.name}: {k!r} must be of type {p['type']}")
        if t is list and not all(isinstance(x, _PY[p["items"]["type"]]) for x in v):
            raise ServiceError(400, f"{tool.name}: {k!r} must be an array of {p['items']['type']}")
        if "enum" in p and v not in p["enum"]:
            raise ServiceError(400, f"{tool.name}: {k!r} must be one of {p['enum']}")
        if t is int and not p["minimum"] <= v <= p["maximum"]:
            raise ServiceError(400, f"{tool.name}: {k!r} must be between {p['minimum']} and {p['maximum']}")


def _s(description: str, **kw: Any) -> dict[str, Any]:
    return {"type": "string", "description": description, **kw}


def _i(description: str, minimum: int = 1, maximum: int = 100, **kw: Any) -> dict[str, Any]:
    return {"type": "integer", "description": description, "minimum": minimum, "maximum": maximum, **kw}


def _obj(description: str) -> dict[str, Any]:
    return {"type": "object", "description": description}


def _strings(description: str) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "description": description}


def _limit(default: int = 20, maximum: int = 100) -> dict[str, Any]:
    return _i("Maximum number of results", maximum=maximum, default=default)


# ---- helpers shared by the mocks ----------------------------------------------------------------


def _ref(prefix: str, args: dict[str, Any], n: int = 10) -> str:
    """Id of a created object: the same call always gets the same id."""
    return prefix + hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:n]


def _find(rows: list[dict], key: str, value: Any, what: str) -> dict:
    hit = next((r for r in rows if r[key] == value), None)
    if hit is None:
        raise ServiceError(404, f"no {what} {value!r}")
    return hit


def _where(rows: list[dict], a: dict[str, Any], *keys: str) -> list[dict]:
    """Rows matching every filter argument given, cut to `limit`."""
    return [r for r in rows if all(r[k] == a[k] for k in keys if k in a)][: a["limit"]]


def _pick(rows: list[dict], *keys: str) -> list[dict]:
    return [{k: r[k] for k in keys} for r in rows]


def _contains(text: str, row: Any) -> bool:
    return text.lower() in json.dumps(row).lower()


# ---- read-only SQL ------------------------------------------------------------------------------

# Statements and clauses that change data or the session. A read replica only answers queries.
_WRITE_SQL = re.compile(
    r"\b(insert|update|delete|merge|upsert|replace\s+into|drop|alter|create|truncate|grant|revoke|copy|call"
    r"|exec|execute|attach|detach|pragma|vacuum|reindex|analyze|lock|set|reset|comment|refresh|into|use)\b",
    re.IGNORECASE,
)
_SQL_NOISE = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", re.DOTALL)
_QUERY_SECONDS = 2.0


def read_only_sql(sql: str, system: str) -> None:
    """Refuse anything but one SELECT (or WITH ... SELECT) statement."""
    code = _SQL_NOISE.sub(" ", sql)  # keywords inside literals, quoted names and comments are data
    statements = [s for s in code.split(";") if s.strip()]
    if len(statements) != 1:
        raise ServiceError(400, f"{system} is read-only: send exactly one statement")
    first = (re.findall(r"\w+", statements[0]) or [""])[0].lower()
    bad = _WRITE_SQL.search(code)
    if first not in ("select", "with") or bad:
        word = bad.group(1) if bad else first or statements[0].strip()[:20]
        raise ServiceError(403, f"{system} is read-only: only SELECT queries are allowed (found {word.upper()})")


def _query(tables: dict[str, dict], sql: str, limit: int, system: str) -> dict[str, Any]:
    read_only_sql(sql, system)
    db = sqlite3.connect(":memory:")
    try:
        for name, t in tables.items():
            db.execute(f"CREATE TABLE {name} ({t['columns']})")
            db.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' * len(t['rows'][0]))})", t["rows"])
        db.execute("PRAGMA query_only = ON")
        deadline = time.monotonic() + _QUERY_SECONDS
        db.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
        cur = db.execute(sql)
        rows = cur.fetchmany(limit)
    except sqlite3.Error as e:
        msg = "canceling statement due to statement timeout" if "interrupted" in str(e) else str(e)
        raise ServiceError(400, f"{system}: {msg}") from None
    finally:
        db.close()
    cols = [d[0] for d in cur.description]
    return {"columns": cols, "rows": [dict(zip(cols, r, strict=True)) for r in rows], "row_count": len(rows)}


def _tables(tables: dict[str, dict]) -> list[dict[str, Any]]:
    return [
        {"table": n, "columns": [c.split()[0] for c in t["columns"].split(", ")], "rows": len(t["rows"])}
        for n, t in tables.items()
    ]


def _sql_tools(prefix: str, tables: dict[str, dict], system: str, where: str, **sql_params: Any) -> tuple[Tool, ...]:
    return (
        Tool(
            f"{prefix}_list_tables",
            "read",
            f"List the tables in {where}, with columns and row counts.",
            {},
            lambda a: _tables(tables),
        ),
        Tool(
            f"{prefix}_query",
            "read",
            f"Run one read-only SQL query (SELECT or WITH ... SELECT) on {where}. Writes and DDL are rejected.",
            {"sql": _s("A single SELECT statement"), **sql_params, "limit": _limit(100, 1000)},
            lambda a: _query(tables, a["sql"], a["limit"], system),
            ("sql",),
        ),
    )


POSTGRES = Service(
    "PostgreSQL (prod read replica)",
    _sql_tools("postgres", DATA["postgres"], "postgres-prod (read replica)", "the production read replica"),
)

SNOWFLAKE = Service(
    "Snowflake",
    _sql_tools(
        "snowflake",
        DATA["snowflake"],
        "Snowflake ANALYTICS",
        "the ANALYTICS warehouse",
        warehouse=_s("Compute warehouse", enum=["REPORTING_XS", "REPORTING_M"], default="REPORTING_XS"),
    ),
)

# ---- Supabase: internal tools database (PostgREST-style) ----------------------------------------


def _supabase_table(a: dict[str, Any]) -> list[dict]:
    rows = DATA["supabase"].get(a["table"])
    if rows is None:
        raise ServiceError(404, f'relation "public.{a["table"]}" does not exist')
    return rows


def _supabase_select(a: dict[str, Any]) -> list[dict]:
    rows = _supabase_table(a)
    eq = a.get("eq", {})
    cols = list(rows[0]) if a["columns"].strip() == "*" else [c.strip() for c in a["columns"].split(",")]
    unknown = [c for c in [*cols, *eq] if c not in rows[0]]
    if unknown:
        raise ServiceError(400, f"column(s) {unknown} do not exist on {a['table']}")
    return _pick(_where(rows, eq | {"limit": a["limit"]}, *eq), *cols)


def _supabase_insert(a: dict[str, Any]) -> dict[str, Any]:
    rows = _supabase_table(a)
    unknown = [c for c in a["row"] if c not in rows[0]]
    if unknown:
        raise ServiceError(400, f"column(s) {unknown} do not exist on {a['table']}")
    return {"status": 201, "table": a["table"], "inserted": {"id": len(rows) + 1} | a["row"]}


SUPABASE = Service(
    "Supabase",
    (
        Tool(
            "supabase_select",
            "read",
            "Select rows from a table of the internal tools database (feature_flags, support_macros, beta_signups).",
            {
                "table": _s("Table name"),
                "columns": _s("'*' or comma-separated column names", default="*"),
                "eq": _obj("Equality filters, column -> value"),
                "limit": _limit(),
            },
            _supabase_select,
            ("table",),
        ),
        Tool(
            "supabase_insert",
            "write",
            "Insert one row into a table of the internal tools database.",
            {"table": _s("Table name"), "row": _obj("Column -> value")},
            _supabase_insert,
            ("table", "row"),
        ),
    ),
    auth_header="apikey",
    auth_format="{secret}",
)

# ---- Upstash Redis ------------------------------------------------------------------------------


def _glob(pattern: str) -> str:
    return re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")


UPSTASH = Service(
    "Upstash Redis",
    (
        Tool(
            "redis_scan",
            "read",
            "List keys matching a glob pattern (* and ? wildcards).",
            {"pattern": _s("Glob, e.g. session:*", default="*")},
            lambda a: sorted(k for k in DATA["upstash"] if re.fullmatch(_glob(a["pattern"]), k)),
        ),
        Tool(
            "redis_get",
            "read",
            "Get the value of a key (null if it does not exist).",
            {"key": _s("Key")},
            lambda a: {"key": a["key"], "value": DATA["upstash"].get(a["key"])},
            ("key",),
        ),
        Tool(
            "redis_set",
            "write",
            "Set a key, optionally with an expiry.",
            {"key": _s("Key"), "value": _s("Value"), "ex_seconds": _i("Expiry in seconds", 1, 30 * 86400)},
            lambda a: {"result": "OK"},
            ("key", "value"),
        ),
    ),
)

# ---- AWS S3 -------------------------------------------------------------------------------------


def _bucket(a: dict[str, Any]) -> dict[str, str]:
    if a["bucket"] not in DATA["s3"]:
        raise ServiceError(404, f"NoSuchBucket: {a['bucket']}")
    return DATA["s3"][a["bucket"]]


def _s3_get(a: dict[str, Any]) -> dict[str, Any]:
    body = _bucket(a).get(a["key"])
    if body is None:
        raise ServiceError(404, f"NoSuchKey: {a['key']}")
    return {"bucket": a["bucket"], "key": a["key"], "content_length": len(body), "body": body}


def _s3_put(a: dict[str, Any]) -> dict[str, Any]:
    _bucket(a)
    return {"bucket": a["bucket"], "key": a["key"], "etag": f'"{_ref("", a, 32)}"'}


AWS_S3 = Service(
    "AWS S3",
    (
        Tool(
            "s3_list_objects",
            "read",
            "List objects in a bucket (acme-exports, acme-deploy-artifacts, acme-public-assets).",
            {"bucket": _s("Bucket name"), "prefix": _s("Key prefix", default="")},
            lambda a: [{"key": k, "size": len(v)} for k, v in _bucket(a).items() if k.startswith(a["prefix"])],
            ("bucket",),
        ),
        Tool(
            "s3_get_object",
            "read",
            "Download an object.",
            {"bucket": _s("Bucket name"), "key": _s("Object key")},
            _s3_get,
            ("bucket", "key"),
        ),
        Tool(
            "s3_put_object",
            "write",
            "Upload an object.",
            {"bucket": _s("Bucket name"), "key": _s("Object key"), "body": _s("Content")},
            _s3_put,
            ("bucket", "key", "body"),
        ),
    ),
    auth_format="AWS4-HMAC-SHA256 Credential={secret}",
)

# ---- Stripe -------------------------------------------------------------------------------------


def _stripe_refund(a: dict[str, Any]) -> dict[str, Any]:
    ch = _find(DATA["stripe"]["charges"], "id", a["charge"], "charge")
    if ch["refunded"]:
        raise ServiceError(400, f"charge {ch['id']} has already been refunded")
    amount = a.get("amount", ch["amount"])
    if amount > ch["amount"]:
        raise ServiceError(400, f"refund amount {amount} exceeds the charge amount {ch['amount']}")
    return {
        "id": _ref("re_", a),
        "object": "refund",
        "charge": ch["id"],
        "amount": amount,
        "currency": ch["currency"],
        "reason": a["reason"],
        "status": "succeeded",
    }


STRIPE = Service(
    "Stripe",
    (
        Tool(
            "stripe_list_charges",
            "read",
            "List charges, newest first, optionally for one customer.",
            {"customer": _s("Customer id (cus_...)"), "limit": _limit(10)},
            lambda a: _where(
                sorted(DATA["stripe"]["charges"], key=lambda c: c["created"], reverse=True), a, "customer"
            ),
        ),
        Tool(
            "stripe_get_customer",
            "read",
            "Retrieve a customer.",
            {"customer": _s("Customer id (cus_...)")},
            lambda a: _find(DATA["stripe"]["customers"], "id", a["customer"], "such customer"),
            ("customer",),
        ),
        Tool(
            "stripe_refund",
            "write",
            "Refund a charge, fully or partially. Money leaves the company account.",
            {
                "charge": _s("Charge id (ch_...)"),
                "amount": _i("Amount in the smallest currency unit; default the full charge", 1, 10**9),
                "reason": _s(
                    "Reason", enum=["duplicate", "fraudulent", "requested_by_customer"], default="requested_by_customer"
                ),
            },
            _stripe_refund,
            ("charge",),
        ),
    ),
)

# ---- Salesforce ---------------------------------------------------------------------------------

SF_STAGES = ["Qualification", "Proposal", "Negotiation", "Closed Won", "Closed Lost"]


def _sf_account(a: dict[str, Any]) -> dict[str, Any]:
    acc = _find(DATA["salesforce"]["accounts"], "Id", a["account_id"], "Account with Id")
    fields = a.get("fields") or list(acc)
    unknown = [f for f in fields if f not in acc]
    if unknown:
        raise ServiceError(400, f"INVALID_FIELD: no such column(s) {unknown} on entity 'Account'")
    opps = [o for o in DATA["salesforce"]["opportunities"] if o["AccountId"] == acc["Id"]]
    return {f: acc[f] for f in fields} | {"Opportunities": opps}


def _sf_update_opp(a: dict[str, Any]) -> dict[str, Any]:
    opp = _find(DATA["salesforce"]["opportunities"], "Id", a["opportunity_id"], "Opportunity with Id")
    return {"id": opp["Id"], "success": True, "updated": {k: a[k] for k in ("StageName", "Amount") if k in a}}


SALESFORCE = Service(
    "Salesforce",
    (
        Tool(
            "salesforce_search_accounts",
            "read",
            "Find accounts whose name contains the given text.",
            {"name": _s("Part of the account name"), "limit": _limit(10)},
            lambda a: _pick(
                [r for r in DATA["salesforce"]["accounts"] if a["name"].lower() in r["Name"].lower()][: a["limit"]],
                *("Id", "Name", "Industry", "BillingCountry"),
            ),
            ("name",),
        ),
        Tool(
            "salesforce_get_account",
            "read",
            "Retrieve an account with its opportunities.",
            {"account_id": _s("Account Id (001...)"), "fields": _strings("Fields to return; default all")},
            _sf_account,
            ("account_id",),
        ),
        Tool(
            "salesforce_update_opportunity",
            "write",
            "Update the stage or amount of an opportunity.",
            {
                "opportunity_id": _s("Opportunity Id (006...)"),
                "StageName": _s("Stage", enum=SF_STAGES),
                "Amount": _i("Amount in USD", 0, 10**9),
            },
            _sf_update_opp,
            ("opportunity_id",),
        ),
    ),
)

# ---- HubSpot ------------------------------------------------------------------------------------

HUBSPOT = Service(
    "HubSpot",
    (
        Tool(
            "hubspot_search_contacts",
            "read",
            "Search contacts by name, email or company.",
            {"query": _s("Search text"), "limit": _limit(10)},
            lambda a: [c for c in DATA["hubspot"] if _contains(a["query"], c)][: a["limit"]],
            ("query",),
        ),
        Tool(
            "hubspot_create_note",
            "write",
            "Add a note to a contact's timeline.",
            {"contact_id": _s("Contact id"), "body": _s("Note text")},
            lambda a: {
                "id": _ref("", a, 8),
                "associations": {"contacts": [_find(DATA["hubspot"], "id", a["contact_id"], "contact")["id"]]},
            },
            ("contact_id", "body"),
        ),
    ),
)

# ---- Zendesk ------------------------------------------------------------------------------------


def _ticket(a: dict[str, Any]) -> dict[str, Any]:
    return _find(DATA["zendesk"], "id", a["ticket_id"], "ticket")


ZENDESK = Service(
    "Zendesk",
    (
        Tool(
            "zendesk_list_tickets",
            "read",
            "List tickets (id, subject, status, priority), optionally by status.",
            {"status": _s("Ticket status", enum=["new", "open", "pending", "solved"]), "limit": _limit()},
            lambda a: _pick(_where(DATA["zendesk"], a, "status"), "id", "subject", "status", "priority"),
        ),
        Tool(
            "zendesk_get_ticket",
            "read",
            "Retrieve a ticket with its full description.",
            {"ticket_id": _i("Ticket id", 1, 10**9)},
            _ticket,
            ("ticket_id",),
        ),
        Tool(
            "zendesk_reply",
            "write",
            "Reply to a ticket, publicly or as an internal note.",
            {
                "ticket_id": _i("Ticket id", 1, 10**9),
                "body": _s("Reply text"),
                "public": {"type": "boolean", "description": "Visible to the requester", "default": True},
            },
            lambda a: {"ticket_id": _ticket(a)["id"], "comment_id": _ref("", a, 8), "public": a["public"]},
            ("ticket_id", "body"),
        ),
    ),
    auth_format="Basic {secret}",
)

# ---- GitHub -------------------------------------------------------------------------------------

GH = DATA["github"]
GH_REPOS = sorted(GH["files"])


def _gh_file(a: dict[str, Any]) -> dict[str, Any]:
    content = GH["files"][a["repo"]].get(a["path"])
    if content is None:
        raise ServiceError(404, f"Not Found: {a['repo']}/{a['path']}")
    return {"repo": a["repo"], "path": a["path"], "ref": a["ref"], "content": content}


def _gh_merge(a: dict[str, Any]) -> dict[str, Any]:
    pr = next((p for p in GH["pulls"] if (p["repo"], p["number"]) == (a["repo"], a["number"])), None)
    if pr is None:
        raise ServiceError(404, f"Not Found: {a['repo']}#{a['number']}")
    return {"merged": True, "sha": _ref("", a, 40), "message": f"Pull request #{pr['number']} ({pr['title']}) merged"}


GITHUB = Service(
    "GitHub",
    (
        Tool(
            "github_list_issues",
            "read",
            "List issues of a repository.",
            {
                "repo": _s("owner/name", enum=GH_REPOS),
                "state": _s("Issue state", enum=["open", "closed", "all"], default="open"),
                "limit": _limit(),
            },
            lambda a: _pick(
                [i for i in GH["issues"] if i["repo"] == a["repo"] and a["state"] in (i["state"], "all")][: a["limit"]],
                *("number", "title", "state", "labels"),
            ),
            ("repo",),
        ),
        Tool(
            "github_get_file",
            "read",
            "Read a file from a repository.",
            {
                "repo": _s("owner/name", enum=GH_REPOS),
                "path": _s("File path"),
                "ref": _s("Branch, tag or commit", default="main"),
            },
            _gh_file,
            ("repo", "path"),
        ),
        Tool(
            "github_create_issue",
            "write",
            "Open an issue.",
            {
                "repo": _s("owner/name", enum=GH_REPOS),
                "title": _s("Title"),
                "body": _s("Markdown body"),
                "labels": _strings("Labels"),
            },
            lambda a: {"number": 300 + int(_ref("", a, 4), 16) % 600, "repo": a["repo"], "title": a["title"]},
            ("repo", "title"),
        ),
        Tool(
            "github_merge_pull_request",
            "admin",
            "Merge a pull request into its base branch.",
            {
                "repo": _s("owner/name", enum=GH_REPOS),
                "number": _i("Pull request number", 1, 10**6),
                "merge_method": _s("Merge method", enum=["merge", "squash", "rebase"], default="squash"),
            },
            _gh_merge,
            ("repo", "number"),
        ),
    ),
)

# ---- Linear -------------------------------------------------------------------------------------

LINEAR_TEAMS = ["ENG", "PLAT", "GROWTH"]
LINEAR_STATES = ["Backlog", "Todo", "In Progress", "In Review", "Done", "Canceled"]

LINEAR = Service(
    "Linear",
    (
        Tool(
            "linear_list_issues",
            "read",
            "List issues of a team, optionally in one workflow state.",
            {
                "team": _s("Team key", enum=LINEAR_TEAMS),
                "state": _s("Workflow state", enum=LINEAR_STATES),
                "limit": _limit(),
            },
            lambda a: _where(DATA["linear"], a, "team", "state"),
            ("team",),
        ),
        Tool(
            "linear_create_issue",
            "write",
            "Create an issue in a team.",
            {
                "team": _s("Team key", enum=LINEAR_TEAMS),
                "title": _s("Title"),
                "description": _s("Markdown description"),
                "priority": _i("0 none, 1 urgent, 2 high, 3 medium, 4 low", 0, 4),
            },
            lambda a: {"identifier": f"{a['team']}-{500 + int(_ref('', a, 4), 16) % 400}", "state": "Todo"},
            ("team", "title"),
        ),
        Tool(
            "linear_update_issue",
            "write",
            "Move an issue to another workflow state.",
            {"identifier": _s("Issue identifier, e.g. ENG-412"), "state": _s("Workflow state", enum=LINEAR_STATES)},
            lambda a: _find(DATA["linear"], "identifier", a["identifier"], "issue") | {"state": a["state"]},
            ("identifier", "state"),
        ),
    ),
)

# ---- Heroku -------------------------------------------------------------------------------------


def _heroku_app(a: dict[str, Any]) -> dict[str, Any]:
    return _find(DATA["heroku"]["apps"], "name", a["app"], "app")


def _heroku_scale(a: dict[str, Any]) -> dict[str, Any]:
    app = _heroku_app(a)
    proc = app["formation"].get(a["process_type"])
    if proc is None:
        raise ServiceError(404, f"no {a['process_type']} process type on {app['name']}")
    return {"app": app["name"], "type": a["process_type"], "quantity": a["quantity"], "size": proc["size"]}


HEROKU = Service(
    "Heroku",
    (
        Tool(
            "heroku_list_apps",
            "read",
            "List apps with region, stack and dyno formation.",
            {},
            lambda a: DATA["heroku"]["apps"],
        ),
        Tool(
            "heroku_get_logs",
            "read",
            "Fetch the most recent log lines of an app.",
            {"app": _s("App name"), "lines": _i("Number of lines", 1, 1500, default=100)},
            lambda a: DATA["heroku"]["logs"][_heroku_app(a)["name"]][-a["lines"] :],
            ("app",),
        ),
        Tool(
            "heroku_restart_dyno",
            "exec",
            "Restart one dyno (e.g. web.1) or, without a dyno, every dyno of the app.",
            {"app": _s("App name"), "dyno": _s("Dyno name, e.g. web.1")},
            lambda a: {"app": _heroku_app(a)["name"], "restarted": a.get("dyno", "all")},
            ("app",),
        ),
        Tool(
            "heroku_scale_formation",
            "admin",
            "Change the number of dynos of one process type.",
            {"app": _s("App name"), "process_type": _s("Process type, e.g. web"), "quantity": _i("Dyno count", 0, 50)},
            _heroku_scale,
            ("app", "process_type", "quantity"),
        ),
    ),
)

# ---- Vercel -------------------------------------------------------------------------------------


def _vercel_promote(a: dict[str, Any]) -> dict[str, Any]:
    d = _find(DATA["vercel"], "uid", a["deployment_id"], "deployment")
    if d["state"] != "READY":
        raise ServiceError(400, f"deployment {d['uid']} is {d['state']}, only READY deployments can be promoted")
    return d | {"target": "production"}


VERCEL = Service(
    "Vercel",
    (
        Tool(
            "vercel_list_deployments",
            "read",
            "List deployments, optionally of one project.",
            {"project": _s("Project name", enum=["acme-marketing", "acme-docs"]), "limit": _limit(10)},
            lambda a: _where(DATA["vercel"], a, "project"),
        ),
        Tool(
            "vercel_promote_deployment",
            "admin",
            "Promote a READY deployment to production.",
            {"deployment_id": _s("Deployment uid (dpl_...)")},
            _vercel_promote,
            ("deployment_id",),
        ),
    ),
)

# ---- Datadog ------------------------------------------------------------------------------------

DD = DATA["datadog"]


def _dd_metrics(a: dict[str, Any]) -> dict[str, Any]:
    if a["query"] not in DD["series"]:
        raise ServiceError(400, f"no series for {a['query']!r}; known: {sorted(DD['series'])}")
    return {"query": a["query"], "from": a["from"], "interval": "15m", "pointlist": DD["series"][a["query"]]}


DATADOG = Service(
    "Datadog",
    (
        Tool(
            "datadog_query_metrics",
            "read",
            "Query a metric time series.",
            {
                "query": _s("Metric query, e.g. avg:system.cpu.user{app:acme-api}"),
                "from": _s("Start, e.g. now-1h", default="now-1h"),
            },
            _dd_metrics,
            ("query",),
        ),
        Tool(
            "datadog_search_logs",
            "read",
            "Search logs by service name or message text.",
            {"query": _s("Search text"), "limit": _limit()},
            lambda a: [x for x in DD["logs"] if _contains(a["query"], [x["service"], x["message"]])][: a["limit"]],
            ("query",),
        ),
        Tool(
            "datadog_mute_monitor",
            "write",
            "Mute a monitor for a number of minutes.",
            {"monitor_id": _i("Monitor id", 1, 10**9), "minutes": _i("Mute duration", 1, 1440)},
            lambda a: _find(DD["monitors"], "id", a["monitor_id"], "monitor") | {"muted_minutes": a["minutes"]},
            ("monitor_id", "minutes"),
        ),
    ),
    auth_header="DD-API-KEY",
    auth_format="{secret}",
)

# ---- PagerDuty ----------------------------------------------------------------------------------

PD = DATA["pagerduty"]

PAGERDUTY = Service(
    "PagerDuty",
    (
        Tool(
            "pagerduty_list_incidents",
            "read",
            "List incidents, optionally by status.",
            {"status": _s("Incident status", enum=["triggered", "acknowledged", "resolved"]), "limit": _limit()},
            lambda a: _where(PD["incidents"], a, "status"),
        ),
        Tool(
            "pagerduty_get_oncall",
            "read",
            "Who is on call for a schedule.",
            {"schedule": _s("Schedule", enum=sorted(PD["oncall"]))},
            lambda a: {"schedule": a["schedule"], **PD["oncall"][a["schedule"]]},
            ("schedule",),
        ),
        Tool(
            "pagerduty_acknowledge_incident",
            "write",
            "Acknowledge an incident.",
            {"incident_id": _s("Incident id")},
            lambda a: _find(PD["incidents"], "id", a["incident_id"], "incident") | {"status": "acknowledged"},
            ("incident_id",),
        ),
    ),
    auth_format="Token token={secret}",
)

# ---- Slack --------------------------------------------------------------------------------------


def _channel(a: dict[str, Any]) -> dict[str, Any]:
    hit = next((c for c in DATA["slack"] if a["channel"] in (c["id"], c["name"], "#" + c["name"])), None)
    if hit is None:
        raise ServiceError(404, "channel_not_found")
    return hit


SLACK = Service(
    "Slack",
    (
        Tool("slack_list_channels", "read", "List public channels.", {}, lambda a: _pick(DATA["slack"], "id", "name")),
        Tool(
            "slack_read_channel",
            "read",
            "Read the latest messages of a channel.",
            {"channel": _s("Channel id or name"), "limit": _limit()},
            lambda a: {"channel": _channel(a)["id"], "messages": _channel(a)["messages"][-a["limit"] :]},
            ("channel",),
        ),
        Tool(
            "slack_post_message",
            "write",
            "Post a message to a channel.",
            {"channel": _s("Channel id or name"), "text": _s("Message text (mrkdwn)")},
            lambda a: {
                "ok": True,
                "channel": _channel(a)["id"],
                "ts": f"1759478{int(_ref('', a, 6), 16) % 1000:03d}.0002",
            },
            ("channel", "text"),
        ),
    ),
)

# ---- Notion -------------------------------------------------------------------------------------

NOTION = Service(
    "Notion",
    (
        Tool(
            "notion_search",
            "read",
            "Search pages by title.",
            {"query": _s("Search text")},
            lambda a: _pick([p for p in DATA["notion"] if _contains(a["query"], p["title"])], "id", "title"),
            ("query",),
        ),
        Tool(
            "notion_get_page",
            "read",
            "Read a page's content.",
            {"page_id": _s("Page id")},
            lambda a: _find(DATA["notion"], "id", a["page_id"], "page"),
            ("page_id",),
        ),
        Tool(
            "notion_create_page",
            "write",
            "Create a page under a parent page.",
            {"parent_id": _s("Parent page id"), "title": _s("Title"), "content": _s("Markdown content")},
            lambda a: {
                "id": _ref("", a, 8),
                "parent": _find(DATA["notion"], "id", a["parent_id"], "page")["id"],
                "title": a["title"],
            },
            ("parent_id", "title"),
        ),
    ),
)

# ---- Google Drive -------------------------------------------------------------------------------

GOOGLE_DRIVE = Service(
    "Google Drive",
    (
        Tool(
            "gdrive_search_files",
            "read",
            "Search files by name.",
            {"query": _s("Part of the file name")},
            lambda a: _pick(
                [f for f in DATA["gdrive"] if _contains(a["query"], f["name"])], "id", "name", "mimeType", "owner"
            ),
            ("query",),
        ),
        Tool(
            "gdrive_read_file",
            "read",
            "Export a file's text.",
            {"file_id": _s("File id")},
            lambda a: _find(DATA["gdrive"], "id", a["file_id"], "file"),
            ("file_id",),
        ),
        Tool(
            "gdrive_share_file",
            "admin",
            "Share a file with someone.",
            {
                "file_id": _s("File id"),
                "email": _s("Recipient email"),
                "role": _s("Access", enum=["reader", "commenter", "writer"]),
            },
            lambda a: {
                "file_id": _find(DATA["gdrive"], "id", a["file_id"], "file")["id"],
                "permission_id": _ref("", a, 12),
                "role": a["role"],
            },
            ("file_id", "email", "role"),
        ),
    ),
)

# ---- Zapier -------------------------------------------------------------------------------------

ZAPIER = Service(
    "Zapier",
    (
        Tool("zapier_list_zaps", "read", "List Zaps with their state.", {}, lambda a: DATA["zapier"]),
        Tool(
            "zapier_trigger_zap",
            "exec",
            "Run a Zap now with an input payload.",
            {"zap_id": _s("Zap id"), "payload": _obj("Input fields for the trigger step")},
            lambda a: {
                "zap": _find(DATA["zapier"], "id", a["zap_id"], "zap")["id"],
                "run_id": _ref("run_", a),
                "status": "success",
            },
            ("zap_id",),
        ),
    ),
    auth_header="X-API-Key",
    auth_format="{secret}",
)

# ---- SendGrid -----------------------------------------------------------------------------------

SENDGRID = Service(
    "SendGrid",
    (
        Tool("sendgrid_list_templates", "read", "List dynamic email templates.", {}, lambda a: DATA["sendgrid"]),
        Tool(
            "sendgrid_send_email",
            "write",
            "Send a templated email to one recipient.",
            {
                "to": _s("Recipient email"),
                "template_id": _s("Template id (d-...)"),
                "dynamic_data": _obj("Template variables"),
            },
            lambda a: {
                "status": 202,
                "template": _find(DATA["sendgrid"], "id", a["template_id"], "template")["name"],
                "message_id": _ref("", a, 22),
            },
            ("to", "template_id"),
        ),
    ),
)

SERVICES: dict[str, Service] = {
    "github": GITHUB,
    "linear": LINEAR,
    "heroku": HEROKU,
    "vercel": VERCEL,
    "postgres": POSTGRES,
    "supabase": SUPABASE,
    "upstash": UPSTASH,
    "s3": AWS_S3,
    "snowflake": SNOWFLAKE,
    "stripe": STRIPE,
    "salesforce": SALESFORCE,
    "hubspot": HUBSPOT,
    "zendesk": ZENDESK,
    "slack": SLACK,
    "notion": NOTION,
    "gdrive": GOOGLE_DRIVE,
    "datadog": DATADOG,
    "pagerduty": PAGERDUTY,
    "zapier": ZAPIER,
    "sendgrid": SENDGRID,
}
