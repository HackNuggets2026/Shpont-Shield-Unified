"""Built-in `company` MCP server: agents use company resources without ever holding a credential.

The gateway authorises each call against the agent's live grant (see resources.py), then performs
it itself, injecting the secret from the gateway's environment. Agents only see results, which
flow back through the normal tool_result checks.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from ..config import Policy
from ..resources import usable
from ..state import StateStore
from ..types import Principal

TOOLS = [
    {
        "name": "list_resources",
        "description": "List the company resources you may use, with their scopes.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "call_api",
        "description": "Call a company SaaS/API resource. Authentication is added for you.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "resource": {"type": "string"},
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                "path": {"type": "string"},
                "body": {"type": "object"},
            },
            "required": ["resource", "path"],
        },
    },
    {
        "name": "run_command",
        "description": "Run a command on a company server resource.",
        "inputSchema": {
            "type": "object",
            "properties": {"resource": {"type": "string"}, "command": {"type": "string"}},
            "required": ["resource", "command"],
        },
    },
]


def scope_for(tool: str, args: dict[str, Any]) -> str | None:
    """The scope a broker call needs; None for calls that touch no resource."""
    if tool == "call_api":
        return "read" if str(args.get("method", "GET")).upper() == "GET" else "write"
    if tool == "run_command":
        return "exec"
    return None


async def handle(req: dict, principal: Principal, policy: Policy, state: StateStore, http: httpx.AsyncClient) -> dict:
    method, rid = req.get("method"), req.get("id")

    def ok(result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def text(t: str, error: bool = False) -> dict:
        return ok({"content": [{"type": "text", "text": t}], "isError": error})

    if method == "initialize":
        return ok(
            {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "company", "version": "0.1"},
                "capabilities": {"tools": {}},
            }
        )
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method != "tools/call":
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}

    params = req.get("params") or {}
    name, args = params.get("name"), params.get("arguments") or {}
    if name == "list_resources":
        mine = usable(policy, state, principal)
        lines = [
            f"{r_id}: {policy.resources[r_id].type} '{policy.resources[r_id].name}' scopes={scopes}"
            for r_id, scopes in sorted(mine.items())
        ]
        return text("\n".join(lines) or "no resources granted")

    res = policy.resources.get(str(args.get("resource")))
    if res is None:  # unreachable after the gateway's grant check, kept for direct callers
        return text("unknown resource", error=True)
    conn = res.connection
    if name == "call_api":
        if res.type not in ("saas", "credential"):
            return text(f"{args['resource']} is a {res.type}, not an API", error=True)
        url = str(conn.get("base_url", "")).rstrip("/") + "/" + str(args.get("path", "")).lstrip("/")
        verb = str(args.get("method", "GET")).upper()
        if url.startswith("mock://"):
            body = conn.get("mock_responses", {}).get(f"{verb} {args.get('path')}", conn.get("mock_default", "200 OK"))
            return text(str(body))
        secret = os.environ.get(str(conn.get("secret_env", "")), "")
        if not secret:
            return text(f"credential for {args['resource']} is not configured on the gateway", error=True)
        header = str(conn.get("auth_header", "Authorization"))
        value = str(conn.get("auth_format", "Bearer {secret}")).format(secret=secret)
        try:
            r = await http.request(verb, url, json=args.get("body"), headers={header: value}, timeout=30)
        except httpx.HTTPError as e:
            return text(f"{type(e).__name__}: {e}", error=True)
        return text(f"HTTP {r.status_code}\n{r.text[:20_000]}", error=r.status_code >= 400)
    if name == "run_command":
        if res.type != "server":
            return text(f"{args['resource']} is a {res.type}, not a server", error=True)
        # Demo: execution is simulated. A real deployment would hand off to a bastion / SSH CA here.
        return text(f"[{conn.get('host', res.name)} {time.strftime('%H:%M:%S')}] $ {args.get('command')}\n(simulated)")
    return text(f"unknown tool {name}", error=True)
