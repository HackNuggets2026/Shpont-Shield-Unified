"""HTTP gateway: OpenAI-compatible chat proxy, MCP proxy, guard API, admin API and dashboard."""

from __future__ import annotations

import asyncio
import contextlib
import csv
import hmac
import io
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse

from .. import resources
from ..config import PolicyStore
from ..controls.access import authenticate
from ..decision import DecisionBackend
from ..engine import ControlLayer, flatten
from ..types import Action, Context, Direction, Principal, Verdict
from . import broker, mcp_demo
from .upstream import UpstreamClient

PANELS = Path(__file__).resolve().parent.parent / "dashboard"
DASHBOARD = PANELS / "index.html"


def authenticate_principal(policy: Any, pid: str) -> Principal:
    key = next((k for k, v in policy.identity.api_keys.items() if v.principal == pid), None)
    return authenticate(policy, key)


def _api_key(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key")


def _verdict_json(v: Verdict) -> dict[str, Any]:
    return {
        "request_id": v.request_id,
        "action": v.action.value,
        "reason": v.reason,
        "text": v.text,
        "policy_version": v.policy_version,
        "latency_ms": {k: round(x, 2) for k, x in v.latency_ms.items()},
        "findings": [
            {
                "control": f.control,
                "category": f.category,
                "action": f.action.value,
                "proposed": f.proposed.value,
                "tier": f.tier,
                "score": round(f.score, 3),
                "detail": f.detail,
                "shadow": f.shadow,
            }
            for f in v.findings
        ],
    }


def _refused(v: Verdict) -> JSONResponse:
    """A redaction that could not be applied precisely: refuse rather than forward the original."""
    return JSONResponse(
        {
            "error": {
                "type": "policy_violation",
                "message": f"cannot redact safely: {v.reason}",
                "request_id": v.request_id,
            }
        },
        status_code=403,
        headers={"x-control-request-id": v.request_id, "x-control-action": "block"},
    )


def _policy_error(v: Verdict) -> JSONResponse:
    return JSONResponse(
        {"error": {"type": "policy_violation", "message": v.reason, "request_id": v.request_id}},
        status_code=v.status_code,
        headers={"x-control-request-id": v.request_id, "x-control-action": v.action.value},
    )


def create_app(
    policy_path: str | Path | None = None,
    backend: DecisionBackend | None = None,
    upstream_client: httpx.AsyncClient | None = None,
    watch: bool = True,
) -> FastAPI:
    store = PolicyStore(policy_path or os.environ.get("ACL_POLICY", "policy.yaml"))
    http = upstream_client or httpx.AsyncClient(timeout=120)
    layer = ControlLayer(store, backend=backend, http=http)

    async def background() -> None:
        last_feed = time.monotonic()
        while True:
            await asyncio.sleep(1)
            try:
                store.poll()
            except Exception:  # noqa: BLE001 - the watcher must survive anything a bad edit throws
                logging.getLogger(__name__).exception("policy poll failed")
            if time.monotonic() - last_feed >= store.policy.signatures.refresh_seconds:
                last_feed = time.monotonic()
                try:
                    await layer.feed.aload(store.base_dir)
                except Exception as e:  # noqa: BLE001 - keep the last good feed
                    layer.feed.errors = [f"refresh failed: {e}"]

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(background()) if watch else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="AI Control Layer", lifespan=lifespan)

    @app.exception_handler(BadRequest)
    async def bad_request(request: Request, exc: BadRequest):
        return JSONResponse({"error": {"type": "invalid_request", "message": str(exc)}}, status_code=400)

    app.state.layer = layer
    app.state.store = store

    @app.middleware("http")
    async def admin_guard(request: Request, call_next):
        token = store.policy.identity.admin_token
        if token and (request.url.path.startswith("/admin") or request.url.path == "/metrics"):
            given = request.headers.get("x-admin-token") or request.query_params.get("token") or ""
            if not hmac.compare_digest(given, token):
                return JSONResponse({"error": "admin token required"}, status_code=401)
        return await call_next(request)

    def upstream() -> UpstreamClient:
        return UpstreamClient(store.policy.upstream, http)

    # ---- agent/app -> model -------------------------------------------------

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await _json_object(request)
        principal = authenticate(store.policy, _api_key(request))
        model = body.get("model")
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages or not all(isinstance(m, dict) for m in messages):
            raise BadRequest("messages must be a non-empty list of objects")
        if model is not None and not isinstance(model, str):
            raise BadRequest("model must be a string")
        messages = [dict(m) for m in messages]

        # The client owns the history and can forge any of it, so every message, every field of it,
        # and the declared tools are inspected on every call. Repeats hit the engine's verdict cache;
        # only the newest message is charged to budgets.
        warnings: list[str] = []
        metered_ctx: Context | None = None
        last = len(messages) - 1
        for i, m in enumerate(messages):
            direction = Direction.TOOL_RESULT if m.get("role") == "tool" else Direction.INPUT
            metered = i == last and direction is Direction.INPUT
            content = m.get("content")
            text = _text(content)
            ctx = Context(principal, direction, text, model=model, channel="chat", metered=metered)
            v = await layer.evaluate(ctx)
            if v.blocked:
                return _policy_error(v)
            if v.action is Action.WARN:
                warnings.append(v.reason)
            if metered:
                metered_ctx = ctx
            if v.action is Action.REDACT:
                if isinstance(content, str):
                    m["content"] = v.text
                elif layer.spans_only(v):
                    cleaned = layer.redact_tree(content, principal, direction)
                    if cleaned is None:
                        return _refused(v)
                    m["content"] = cleaned
                elif isinstance(content, list):  # withheld as a whole: replace the text, keep other parts
                    m["content"] = [{"type": "text", "text": v.text}, *(p for p in content if not _is_text_part(p))]
                else:
                    m["content"] = v.text

            # Everything else: tool_calls, function_call, name, refusal, image URLs, extra keys on parts.
            rest = {k: val for k, val in m.items() if k not in ("role", "content")}
            parts = []
            if isinstance(content, list):
                parts = [{k: val for k, val in p.items() if k != "text"} if _is_text_part(p) else p for p in content]
            if rest or parts:
                rctx = Context(principal, direction, flatten([rest, parts]), model=model, channel="chat", metered=False)
                rv = await layer.evaluate(rctx)
                if rv.blocked:
                    return _policy_error(rv)
                if rv.action is Action.REDACT:
                    fields = layer.redact_tree(rest, principal, direction) if layer.spans_only(rv) else None
                    if fields is None or layer.redact_tree(parts, principal, direction) != parts:
                        return _refused(rv)
                    messages[i] = {**{k: m[k] for k in ("role", "content") if k in m}, **fields}

        for key in ("tools", "functions"):  # `functions` is the legacy spelling of `tools`
            declared = body.get(key)
            if declared is None:
                continue
            if not isinstance(declared, list):
                raise BadRequest(f"{key} must be a list")
            kept = []
            for tool in declared:
                tctx = Context(
                    principal, Direction.TOOL_DESCRIPTION, flatten(tool), model=model, channel="chat", metered=False
                )
                tv = await layer.evaluate(tctx)
                if tv.blocked:
                    return _policy_error(tv)
                if tv.action is Action.REDACT:
                    cleaned = (
                        layer.redact_tree(tool, principal, Direction.TOOL_DESCRIPTION) if layer.spans_only(tv) else None
                    )
                    if cleaned is None:
                        return _refused(tv)
                    tool = cleaned
                kept.append(tool)
            body = {**body, key: kept}
        if metered_ctx is None:  # turn ends in a tool result, already inspected: gates and budgets still apply
            metered_ctx = Context(
                principal, Direction.INPUT, _text(messages[-1].get("content")), model=model, channel="chat"
            )
            v = await layer.gate(metered_ctx)
            if v.blocked:
                return _policy_error(v)

        # Backstop: every remaining field of the outgoing body (response_format, tool_choice, metadata,
        # roles, anything a future API adds) gets the deterministic detectors before it leaves.
        outgoing = {**body, "messages": messages}
        sv = await layer.evaluate(
            Context(principal, Direction.INPUT, flatten(outgoing), model=model, channel="chat", metered=False),
            semantic=False,
        )
        if sv.blocked:
            return _policy_error(sv)
        if sv.action is Action.REDACT:
            cleaned = layer.redact_tree(outgoing, principal, Direction.INPUT)
            if cleaned is None:
                return _refused(sv)
            outgoing = cleaned

        try:
            completion = await upstream().chat(outgoing)
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
            return JSONResponse(
                {"error": {"type": "upstream_error", "message": f"{type(e).__name__}: {e}"}}, status_code=502
            )
        cost = layer.ledger.record(
            metered_ctx,
            layer.policy_for(principal.team),
            model or "unknown",
            completion.input_tokens,
            completion.output_tokens,
            completion.compute_seconds,
        )

        out_ctx = Context(
            principal,
            Direction.OUTPUT,
            completion.content,
            model=model,
            channel="chat",
            request_id=metered_ctx.request_id,
        )
        ov = await layer.evaluate(
            out_ctx, {"usd": round(cost, 6), "tokens": completion.input_tokens + completion.output_tokens}
        )
        content, finish = (ov.text if ov.action is Action.REDACT else completion.content), "stop"
        if ov.blocked:
            content, finish = f"[Response withheld by policy: {ov.reason}]", "content_filter"
        if ov.action is Action.WARN:
            warnings.append(ov.reason)

        resp = {
            "id": f"chatcmpl-{metered_ctx.request_id}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}],
            "usage": {
                "prompt_tokens": completion.input_tokens,
                "completion_tokens": completion.output_tokens,
                "total_tokens": completion.input_tokens + completion.output_tokens,
            },
            "control": {
                "input_request_id": metered_ctx.request_id,
                "output_action": ov.action.value,
                "warnings": warnings,
            },
        }
        headers = {"x-control-request-id": metered_ctx.request_id, "x-control-action": ov.action.value}
        if body.get("stream"):
            # The full reply must be inspected before release, so it is sent as a single chunk.
            chunk = {
                **resp,
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": finish}],
            }
            payload = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"
            return StreamingResponse(iter([payload]), media_type="text/event-stream", headers=headers)
        return JSONResponse(resp, headers=headers)

    # ---- agent -> MCP tools -------------------------------------------------

    @app.post("/mcp/{server}")
    async def mcp(server: str, request: Request):
        def rpc(rid: Any, code: int, message: str, data: dict | None = None) -> JSONResponse:
            err: dict[str, Any] = {"code": code, "message": message}
            if data:
                err["data"] = data
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": err})

        try:
            req = await request.json()
        except ValueError:
            return rpc(None, -32700, "parse error")
        if not isinstance(req, dict):
            return rpc(None, -32600, "batch requests are not supported" if isinstance(req, list) else "invalid request")
        rid, method, params = req.get("id"), req.get("method"), req.get("params", {})
        params = {} if params is None else params
        if not isinstance(method, str) or not isinstance(params, dict):
            return rpc(rid, -32600, "invalid request")
        principal = authenticate(store.policy, _api_key(request))
        target = store.policy.upstream.mcp_servers.get(server)
        if target is None:
            return rpc(rid, -32004, f"unknown MCP server {server!r}")

        def blocked(v: Verdict) -> JSONResponse:
            return rpc(
                rid, -32001, f"blocked by policy: {v.reason}", {"request_id": v.request_id, "action": v.action.value}
            )

        policy = layer.policy_for(principal.team)
        # A catalogued MCP server is a resource: agents need their owner's grant to reach it.
        # Agents may not reach uncatalogued servers at all; the company broker checks per call.
        server_resource = next(
            (
                r_id
                for r_id, r in policy.resources.items()
                if r.type == "mcp_server" and r.connection.get("server") == server
            ),
            None,
        )
        if server_resource is None and principal.kind == "agent" and target != "broker":
            server_resource = f"mcp:{server}"

        async def forward(r: dict) -> dict:
            if target == "builtin":
                return mcp_demo.handle(r)
            if target == "broker":
                return await broker.handle(r, principal, policy, layer.state, http)
            resp = await http.post(target, json=r, headers={"accept": "application/json"})
            data = resp.json()
            if not isinstance(data, dict):
                raise ValueError("MCP server returned a non-object")
            return data

        name: str | None = None
        request_id: str | None = None
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if not isinstance(name, str) or not isinstance(args, dict):
                return rpc(rid, -32602, "tools/call needs a string name and object arguments")
            resource, scope = server_resource, None
            if target == "broker" and broker.scope_for(name, args):
                resource, scope = str(args.get("resource")), broker.scope_for(name, args)
            # All of params (name, _meta, ...), not only the arguments, reaches the server.
            ctx = Context(
                principal,
                Direction.TOOL_CALL,
                flatten(params),
                tool=name,
                tool_args=args,
                channel="mcp",
                resource=resource,
                scope=scope,
            )
            v = await layer.evaluate(ctx, {"server": server})
            if v.blocked:
                return blocked(v)
            if v.action is Action.REDACT:
                cleaned = layer.redact_tree(params, principal, Direction.TOOL_CALL) if layer.spans_only(v) else None
                if cleaned is None:
                    return blocked(v)  # cannot be cut out of the call, so it cannot go ahead
                req = {**req, "params": cleaned}
            request_id = ctx.request_id
        elif method not in ("initialize", "tools/list"):
            # resources/read, prompts/get, ...: their params reach the server too (and gates apply).
            # Not metered: pings, notifications and reads are protocol traffic, not tool spend.
            ctx = Context(
                principal, Direction.TOOL_CALL, flatten(params), channel="mcp", metered=False, resource=server_resource
            )
            v = await layer.evaluate(ctx, {"server": server, "method": method})
            if v.blocked:
                return blocked(v)
            if v.action is Action.REDACT:
                cleaned = layer.redact_tree(params, principal, Direction.TOOL_CALL) if layer.spans_only(v) else None
                if cleaned is None:
                    return blocked(v)
                req = {**req, "params": cleaned}
            request_id = ctx.request_id
        else:  # initialize / tools/list: identity and server access still apply
            gv = await layer.gate(
                Context(principal, Direction.TOOL_CALL, "", channel="mcp", metered=False, resource=server_resource)
            )
            if gv.blocked:
                return blocked(gv)

        try:
            resp = await forward(req)
        except (httpx.HTTPError, ValueError) as e:
            return rpc(rid, -32002, f"MCP server unavailable: {type(e).__name__}: {e}")
        result = resp.get("result")
        if not isinstance(result, dict) or method == "initialize":
            return JSONResponse(resp)

        if method == "tools/list":
            kept = []
            for tool in result.get("tools", []):
                if not isinstance(tool, dict):
                    continue
                # Name, description and every schema string: poisoning hides in parameter descriptions too.
                tctx = Context(
                    principal, Direction.TOOL_DESCRIPTION, flatten(tool), tool=tool.get("name"), channel="mcp"
                )
                tv = await layer.evaluate(tctx, {"server": server})
                if tv.blocked:
                    continue
                if tv.action is Action.REDACT:
                    tool = (
                        layer.redact_tree(tool, principal, Direction.TOOL_DESCRIPTION) if layer.spans_only(tv) else None
                    )
                    if tool is None:
                        continue
                kept.append(tool)
            return JSONResponse({**resp, "result": {**result, "tools": kept}})

        # Every other result (tool output, resources, prompts) flows back into the agent: inspect all of it.
        rctx = Context(
            principal,
            Direction.TOOL_RESULT,
            flatten(result),
            tool=name,
            channel="mcp",
            request_id=request_id or uuid.uuid4().hex[:16],
        )
        rv = await layer.evaluate(rctx, {"server": server, "method": method})
        if rv.blocked:
            return blocked(rv)
        if rv.action is Action.REDACT:
            cleaned = layer.redact_tree(result, principal, Direction.TOOL_RESULT) if layer.spans_only(rv) else None
            if cleaned is not None:
                result = cleaned
            elif method == "tools/call":
                result = {"content": [{"type": "text", "text": f"[withheld by policy: {rv.reason}]"}], "isError": True}
            else:
                return blocked(rv)
        return JSONResponse({**resp, "result": result})

    # ---- SDK / sidecar check --------------------------------------------------

    @app.post("/v1/guard")
    async def guard(request: Request):
        body = await _json_object(request)
        principal = authenticate(store.policy, _api_key(request))
        try:
            direction = Direction(body.get("direction", "input"))
        except ValueError:
            return JSONResponse({"error": f"direction must be one of {[d.value for d in Direction]}"}, status_code=400)
        ctx = Context(
            principal,
            direction,
            _text(body.get("text", "")),
            model=body.get("model"),
            tool=body.get("tool"),
            channel="sdk",
        )
        v = await layer.evaluate(ctx)
        return JSONResponse(_verdict_json(v), status_code=v.status_code if v.blocked else 200)

    # ---- employee panel -------------------------------------------------------

    def employee(request: Request):
        p = authenticate(store.policy, _api_key(request))
        if not p.authenticated:
            raise HTTPException(401, "sign in with your personal API key")
        if p.kind != "human":
            raise HTTPException(403, "agents cannot use the employee panel")
        return p

    def resource_view(rid: str, r: Any) -> dict[str, Any]:
        return {
            "id": rid,
            "type": r.type,
            "name": r.name,
            "description": r.description,
            "sensitivity": r.sensitivity,
            "scopes": r.scopes,
            "max_grant_hours": r.max_grant_hours,
            "suspended": resources.suspended(store.policy, layer.state, rid),
        }

    @app.get("/me/api/profile")
    async def me_profile(request: Request):
        p = employee(request)
        snap = layer.ledger.snapshot(layer.policy_for(p.team))
        mine = [r for r in snap["scopes"] if (r["scope"], r["key"]) in (("principal", p.id), ("team", p.team))]
        return {
            "principal": p.id,
            "team": p.team,
            "role": p.role,
            "agents": resources.agents_of(store.policy, p.id),
            "budgets": mine,
            "monitoring_notice": (
                "AI use is inspected by the company AI control layer: prompts, replies and tool calls are checked "
                "for confidential data, client PII and policy violations, and decisions are logged."
            ),
            "pii_override_allowed": p.role in store.policy.pii_model.override_roles,
        }

    @app.get("/me/api/resources")
    async def me_resources(request: Request):
        p = employee(request)
        policy = store.policy
        agents = [a["principal"] for a in resources.agents_of(policy, p.id)]
        out = []
        for rid, r in resources.entitled(policy, p).items():
            view = resource_view(rid, r)
            view["grants"] = {
                a: layer.state.grants.get(a, {}).get(rid)
                | {"active": bool(resources.active_grant(policy, layer.state, authenticate_principal(policy, a), rid))}
                for a in agents
                if rid in layer.state.grants.get(a, {})
            }
            out.append(view)
        return {"agents": agents, "resources": out}

    @app.post("/me/api/grants")
    async def me_grant(request: Request):
        p = employee(request)
        body = await _json_object(request)
        hours = body.get("hours")
        try:
            g = resources.grant(
                store.policy,
                layer.state,
                p,
                str(body.get("agent")),
                str(body.get("resource")),
                [str(x) for x in body.get("scopes") or []],
                float(hours) if hours is not None else None,
            )
        except (resources.GrantError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        layer.audit.note("grant", p.id, agent=body.get("agent"), resource=body.get("resource"), grant=g)
        return g

    @app.delete("/me/api/grants/{agent}/{rid}")
    async def me_revoke(agent: str, rid: str, request: Request):
        p = employee(request)
        if agent not in {a["principal"] for a in resources.agents_of(store.policy, p.id)}:
            return JSONResponse({"error": f"{agent!r} is not one of your agents"}, status_code=403)
        removed = resources.revoke(layer.state, agent, rid)
        if removed:
            layer.audit.note("revoke", p.id, agent=agent, resource=rid)
        return {"revoked": removed}

    @app.get("/me/api/activity")
    async def me_activity(request: Request, limit: int = 50):
        p = employee(request)
        mine = {p.id} | {a["principal"] for a in resources.agents_of(store.policy, p.id)}
        rows = [
            {k: e.get(k) for k in ("ts", "principal", "channel", "direction", "model", "tool", "action", "reason")}
            for e in reversed(layer.audit.events)
            if e["principal"] in mine
        ]
        return rows[:limit]

    @app.get("/me", response_class=HTMLResponse)
    async def me_page():
        return (PANELS / "employee.html").read_text()

    # ---- reporting ------------------------------------------------------------

    @app.get("/admin/summary")
    async def summary():
        a = layer.audit
        p = store.policy
        controls = [
            {
                "name": n,
                "kind": "deterministic",
                "enabled": c.enabled,
                "mode": c.mode.value,
                "shadow": c.shadow,
                "hits": a.controls.get(n, 0),
            }
            for n, c in (
                ("secrets", p.secrets),
                ("pii", p.pii),
                ("signatures", p.signatures),
                ("tool_access", p.tool_access),
            )
        ] + [
            {
                "name": n,
                "kind": "semantic",
                "enabled": c.enabled,
                "mode": c.mode.value,
                "shadow": c.shadow,
                "hits": a.controls.get(n, 0),
            }
            for n, c in p.semantic_controls.items()
        ]
        return {
            "policy": {"name": p.name, "version": p.version, "reloads": store.reloads, "last_error": store.last_error},
            "feed": {
                "version": layer.feed.feed_version,
                "signatures": len(layer.feed.signatures),
                "loaded_at": layer.feed.loaded_at,
                "errors": layer.feed.errors,
            },
            "semantic": {
                "backend": p.semantic.backend,
                "fast_model": p.semantic.fast_model,
                "deep_model": p.semantic.deep_model,
                "fail_mode": p.semantic.fail_mode,
            },
            "totals": {"events": a.total, **a.actions},
            "controls": controls,
            "top_categories": a.categories.most_common(15),
            "shadow_would_have": a.shadow_hits.most_common(15),
            "principals": a.by_principal.most_common(30),
            "budgets": layer.ledger.snapshot(p),
            "latency_ms": a.latency_summary(),
        }

    @app.get("/admin/events")
    async def events(limit: int = 100, action: str | None = None, control: str | None = None):
        out = [
            e
            for e in reversed(layer.audit.events)
            if (not action or e["action"] == action)
            and (not control or any(f["control"] == control for f in e["findings"]))
        ]
        return out[:limit]

    @app.get("/admin/audit/export")
    async def export(format: str = "jsonl"):
        rows = list(layer.audit.events)
        if format == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(
                [
                    "ts",
                    "request_id",
                    "principal",
                    "team",
                    "channel",
                    "direction",
                    "model",
                    "tool",
                    "action",
                    "status_code",
                    "reason",
                    "controls",
                    "policy_version",
                ]
            )
            for e in rows:
                w.writerow(
                    [
                        e["ts"],
                        e["request_id"],
                        e["principal"],
                        e["team"],
                        e["channel"],
                        e["direction"],
                        e["model"],
                        e["tool"],
                        e["action"],
                        e["status_code"],
                        e["reason"],
                        ";".join(f"{f['control']}/{f['category']}" for f in e["findings"]),
                        e["policy_version"],
                    ]
                )
            return Response(
                buf.getvalue(), media_type="text/csv", headers={"content-disposition": "attachment; filename=audit.csv"}
            )
        return Response(
            "".join(json.dumps(e) + "\n" for e in rows),
            media_type="application/x-ndjson",
            headers={"content-disposition": "attachment; filename=audit.jsonl"},
        )

    @app.get("/admin/policy")
    async def policy_view():
        p = store.policy
        data = p.model_dump(mode="json", by_alias=True)
        data["identity"]["api_keys"] = {k[:4] + "…": v for k, v in data["identity"]["api_keys"].items()}
        data["identity"]["admin_token"] = "set" if p.identity.admin_token else None
        return {"version": p.version, "reloads": store.reloads, "last_error": store.last_error, "policy": data}

    @app.post("/admin/try")
    async def try_as(request: Request):
        """Dashboard playground: evaluate text as a named principal without handing out their key."""
        body = await _json_object(request)
        key = next((k for k, v in store.policy.identity.api_keys.items() if v.principal == body.get("principal")), None)
        principal = authenticate(store.policy, key)
        try:
            direction = Direction(body.get("direction", "input"))
        except ValueError:
            return JSONResponse({"error": "bad direction"}, status_code=400)
        v = await layer.evaluate(Context(principal, direction, _text(body.get("text", "")), channel="dashboard"))
        return JSONResponse(_verdict_json(v), status_code=v.status_code if v.blocked else 200)

    @app.post("/admin/policy/reload")
    async def policy_reload():
        ok = store.reload()
        return JSONResponse(
            {"ok": ok, "version": store.policy.version, "error": store.last_error}, status_code=200 if ok else 422
        )

    @app.get("/admin/risk")
    async def risk_overview():
        return {
            "principals": layer.risk.overview(store.policy),
            "levels": store.policy.insider_risk.levels.model_dump(),
        }

    @app.post("/admin/risk/{pid}")
    async def risk_set(pid: str, request: Request):
        """Set a manual floor level (`normal` clears it), and optionally reset the score after review."""
        body = await _json_object(request)
        level = body.get("level")
        if level not in ("normal", "watch", "restricted"):
            raise BadRequest("level must be normal, watch or restricted")
        if not any(k.principal == pid for k in store.policy.identity.api_keys.values()):
            return JSONResponse({"error": f"unknown principal {pid!r}"}, status_code=404)
        reason = str(body.get("reason") or "")
        if level == "normal":
            layer.state.watch.pop(pid, None)
        else:
            layer.state.watch[pid] = {"level": level, "reason": reason, "at": time.time()}
        if body.get("reset_score"):
            layer.risk.reset(pid)
        layer.state.save()
        layer.audit.note(
            "risk_level",
            "security",
            principal=pid,
            level=level,
            reason=reason,
            reset_score=bool(body.get("reset_score")),
        )
        return {"principal": pid, "level": level}

    @app.get("/admin/alerts")
    async def alerts(limit: int = 100):
        return {"alerts": list(reversed(layer.risk.alerts))[:limit], "sink_errors": list(layer.risk.sink_errors)}

    @app.get("/admin/grants")
    async def all_grants():
        policy = store.policy
        rows = []
        for agent, gs in layer.state.grants.items():
            ap = authenticate_principal(policy, agent)
            for rid, g in gs.items():
                rows.append(
                    {
                        "agent": agent,
                        "owner": ap.owner,
                        "resource": rid,
                        **g,
                        "active": resources.active_grant(policy, layer.state, ap, rid) is not None,
                    }
                )
        return {
            "grants": rows,
            "resources": [
                resource_view(rid, r) | {"entitled": r.entitled.model_dump()} for rid, r in policy.resources.items()
            ],
        }

    @app.delete("/admin/grants/{agent}/{rid}")
    async def admin_revoke(agent: str, rid: str):
        removed = resources.revoke(layer.state, agent, rid)
        if removed:
            layer.audit.note("revoke", "security", agent=agent, resource=rid)
        return {"revoked": removed}

    @app.post("/admin/resources/{rid}/suspend")
    async def suspend(rid: str, request: Request):
        body = await _json_object(request)
        if rid not in store.policy.resources:
            return JSONResponse({"error": f"unknown resource {rid!r}"}, status_code=404)
        if body.get("suspended", True):
            layer.state.suspended[rid] = {"reason": str(body.get("reason") or ""), "at": time.time()}
        else:
            layer.state.suspended.pop(rid, None)
        layer.state.save()
        layer.audit.note("suspend", "security", resource=rid, suspended=bool(body.get("suspended", True)))
        return {"resource": rid, "suspended": rid in layer.state.suspended}

    @app.get("/security", response_class=HTMLResponse)
    async def security_page():
        return DASHBOARD.read_text()

    @app.get("/metrics")
    async def metrics():
        a = layer.audit
        lines = ["# TYPE acl_decisions_total counter"]
        lines += [f'acl_decisions_total{{action="{k}"}} {v}' for k, v in a.actions.items()]
        lines += ["# TYPE acl_findings_total counter"]
        lines += [f'acl_findings_total{{control="{k}"}} {v}' for k, v in a.controls.items()]
        lines += ["# TYPE acl_latency_ms summary"]
        for stage, s in a.latency_summary().items():
            lines += [
                f'acl_latency_ms{{stage="{stage}",quantile="0.5"}} {s["p50"]}',
                f'acl_latency_ms{{stage="{stage}",quantile="0.95"}} {s["p95"]}',
            ]
        snap = layer.ledger.snapshot(store.policy)
        lines += ["# TYPE acl_budget_usd gauge"]
        lines += [f'acl_budget_usd{{scope="{r["scope"]}",key="{r["key"]}"}} {r["usd"]}' for r in snap["scopes"]]
        return PlainTextResponse("\n".join(lines) + "\n")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        return DASHBOARD.read_text() if DASHBOARD.exists() else "<p>dashboard not built</p>"

    return app


class BadRequest(Exception):
    pass


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError as e:
        raise BadRequest(f"body is not valid JSON: {e}") from e
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    return body


def _is_text_part(p: Any) -> bool:
    return isinstance(p, dict) and p.get("type") == "text"


def _text(content: Any) -> str:
    """The text of OpenAI content: a string, the text parts of a list, or any other shape flattened."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(flatten(p.get("text", "")) for p in content if _is_text_part(p))
    return flatten(content)
