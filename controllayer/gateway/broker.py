"""Built-in `company` MCP server: agents use company services without ever holding a credential.

Every service in the catalog contributes its own tools (controllayer/services.py). The gateway
authorises each call against the caller's live grant for the tool's scope (see resources.py),
then performs it itself, injecting the secret from the gateway's environment. Callers only see
results, which flow back through the normal tool_result checks.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .. import services
from ..config import Policy
from ..resources import usable
from ..state import StateStore
from ..types import Principal

LIST_RESOURCES = {
    "name": "list_resources",
    "description": "List the company services you may use now, with your scopes and the tools they unlock.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}


def catalog(policy: Policy) -> dict[str, tuple[str, services.Tool]]:
    """Tool name -> (resource id, tool) for every service in the policy's catalog."""
    return {
        t.name: (rid, t)
        for rid, r in policy.resources.items()
        if r.type == "service"
        for t in services.SERVICES[r.connection["service"]].tools
    }


def target(policy: Policy, tool: str) -> tuple[str, str] | None:
    """The resource and scope a tool call needs; None for tools that touch no resource."""
    hit = catalog(policy).get(tool)
    return (hit[0], hit[1].scope) if hit else None


def _secret(policy: Policy, rid: str) -> str:
    # Every backend is a mock, so an unset variable falls back to a fixed demo credential.
    return os.environ.get(policy.resources[rid].connection["secret_env"]) or f"demo-{rid}-credential"


def handle(req: dict, principal: Principal, policy: Policy, state: StateStore) -> dict:
    method, rid = req.get("method"), req.get("id")

    def ok(result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def text(t: str, error: bool = False) -> dict:
        return ok({"content": [{"type": "text", "text": t}], "isError": error})

    if method == "initialize":
        return ok(
            {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "company", "version": "0.2"},
                "capabilities": {"tools": {}},
            }
        )
    if method == "ping":
        return ok({})
    tools = catalog(policy)
    mine = usable(policy, state, principal)
    if method == "tools/list":
        offered = [t.spec() for res, t in tools.values() if t.scope in mine.get(res, ())]
        return ok({"tools": [LIST_RESOURCES, *offered]})
    if method != "tools/call":
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}

    params = req.get("params") or {}
    name, args = params.get("name"), params.get("arguments") or {}
    if name == "list_resources":
        lines = []
        for res_id, scopes in sorted(mine.items()):
            res = policy.resources[res_id]
            line = f"{res_id}: {res.name} scopes={scopes}"
            if res.type == "mcp_server":
                line += f" (MCP server at /mcp/{res.connection['server']})"
            else:
                line += f" tools={[t.name for r, t in tools.values() if r == res_id and t.scope in scopes]}"
            lines.append(line)
        return text("\n".join(lines) or "no resources granted")
    if name not in tools:
        return text(f"unknown tool {name}", error=True)
    res_id, tool = tools[name]
    svc = services.SERVICES[policy.resources[res_id].connection["service"]]
    headers = {svc.auth_header: svc.auth_format.format(secret=_secret(policy, res_id))}
    try:
        out = services.call(svc, tool, args, headers)
    except services.ServiceError as e:
        return text(f"{svc.title}: {e}", error=True)
    return text(json.dumps(out, ensure_ascii=False))
