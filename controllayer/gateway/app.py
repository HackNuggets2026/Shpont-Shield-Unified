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
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse

from ..config import PolicyStore
from ..controls.access import authenticate
from ..decision import DecisionBackend
from ..engine import ControlLayer, flatten
from ..types import Action, Context, Direction, Verdict
from . import governance, mcp_demo
from .upstream import UpstreamClient

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"


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
    layer = ControlLayer(store, backend=backend)
    http = upstream_client or httpx.AsyncClient(timeout=120)

    async def mcp_forward(target: str, r: dict) -> dict:
        if target == "builtin":
            return mcp_demo.handle(r)
        resp = await http.post(target, json=r, headers={"accept": "application/json"})
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("MCP server returned a non-object")
        return data

    async def reclaim(lease: dict, reason: str) -> bool:
        """Stop a leased resource through its MCP server, then close the lease. False if it cannot be stopped."""
        policy = store.policy
        req = layer.leases.reclaim_request(lease, policy)
        target = policy.upstream.mcp_servers.get(lease["server"] or "")
        if req is None or target is None:
            return False
        try:
            resp = await mcp_forward(target, req)
        except (httpx.HTTPError, ValueError) as e:
            logging.getLogger(__name__).error("reclaim of %s failed: %s", lease["id"], e)
            return False
        if "error" in resp:
            return False
        layer.leases.close(lease["id"], policy, reason)
        return True

    app_reclaim = reclaim

    async def background() -> None:
        last_feed = last_sweep = time.monotonic()
        while True:
            await asyncio.sleep(1)
            try:
                store.poll()
            except Exception:  # noqa: BLE001 - the watcher must survive anything a bad edit throws
                logging.getLogger(__name__).exception("policy poll failed")
            if time.monotonic() - last_sweep >= 10:
                last_sweep = time.monotonic()
                try:
                    for lease in layer.leases.sweep(store.policy):
                        await reclaim(lease, "reclaimed: " + ",".join(lease["flags"]))
                except Exception:  # noqa: BLE001 - a sweep failure must not stop the policy watcher
                    logging.getLogger(__name__).exception("lease sweep failed")
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

    def attribution(request: Request, principal) -> dict[str, Any]:
        """Workflow, task and session from the x-acl-* headers; a session keeps its declared workflow."""
        h = request.headers
        wf = (h.get("x-acl-workflow") or "").strip() or None
        attrs: dict[str, Any] = {
            "workflow": wf,
            "workflow_source": "declared" if wf else None,
            "task_id": (h.get("x-acl-task") or "").strip()[:200] or None,
            "session_id": (h.get("x-acl-session") or "").strip()[:200] or None,
            "client": f"{request.client.host if request.client else '?'}|{h.get('user-agent', '')[:80]}",
        }
        ctx = layer.attribute(Context(principal, Direction.INPUT, "", **attrs))
        attrs.update(workflow=ctx.workflow, workflow_source=ctx.workflow_source)
        return attrs

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
        attrs = attribution(request, principal)

        # Past the downgrade threshold of a budget, a cheaper model answers instead of a refusal.
        downgraded_from = None
        if principal.authenticated and isinstance(model, str):
            probe = Context(principal, Direction.INPUT, "", model=model, channel="chat", **attrs)
            cheaper = layer.ledger.downgrade(probe, layer.policy_for(principal.team), model)
            if cheaper:
                downgraded_from, model = model, cheaper
                body = {**body, "model": model}

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
            ctx = Context(principal, direction, text, model=model, channel="chat", **attrs, metered=metered)
            v = await layer.evaluate(ctx)
            if v.blocked:
                return _policy_error(v)
            if v.action is Action.WARN:
                warnings.append(v.reason)
            if metered:
                metered_ctx = ctx
                # The classifier may have attributed an unlabeled prompt; the reply and costs follow it.
                attrs.update(workflow=ctx.workflow, workflow_source=ctx.workflow_source)
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
                rctx = Context(
                    principal, direction, flatten([rest, parts]), model=model, channel="chat", **attrs, metered=False
                )
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
                    principal,
                    Direction.TOOL_DESCRIPTION,
                    flatten(tool),
                    model=model,
                    channel="chat",
                    **attrs,
                    metered=False,
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
                principal, Direction.INPUT, _text(messages[-1].get("content")), model=model, channel="chat", **attrs
            )
            v = await layer.gate(metered_ctx)
            if v.blocked:
                return _policy_error(v)

        # Backstop: every remaining field of the outgoing body (response_format, tool_choice, metadata,
        # roles, anything a future API adds) gets the deterministic detectors before it leaves.
        outgoing = {**body, "messages": messages}
        sv = await layer.evaluate(
            Context(principal, Direction.INPUT, flatten(outgoing), model=model, channel="chat", **attrs, metered=False),
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
            **attrs,
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
                "workflow": metered_ctx.workflow,
                "workflow_source": metered_ctx.workflow_source,
                "usd": round(cost, 6),
                **({"downgraded_from": downgraded_from} if downgraded_from else {}),
            },
        }
        headers = {"x-control-request-id": metered_ctx.request_id, "x-control-action": ov.action.value}
        if downgraded_from:
            headers["x-control-downgraded-from"] = downgraded_from
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
        attrs = attribution(request, principal)
        target = store.policy.upstream.mcp_servers.get(server)
        if target is None:
            return rpc(rid, -32004, f"unknown MCP server {server!r}")

        def blocked(v: Verdict) -> JSONResponse:
            return rpc(
                rid, -32001, f"blocked by policy: {v.reason}", {"request_id": v.request_id, "action": v.action.value}
            )

        name: str | None = None
        call_ctx: Context | None = None
        request_id: str | None = None
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if not isinstance(name, str) or not isinstance(args, dict):
                return rpc(rid, -32602, "tools/call needs a string name and object arguments")
            # All of params (name, _meta, ...), not only the arguments, reaches the server.
            ctx = Context(
                principal, Direction.TOOL_CALL, flatten(params), tool=name, tool_args=args, channel="mcp", **attrs
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
            call_ctx = ctx
        elif method not in ("initialize", "tools/list"):
            # resources/read, prompts/get, ...: their params reach the server too (and gates apply).
            # Not metered: pings, notifications and reads are protocol traffic, not tool spend.
            ctx = Context(principal, Direction.TOOL_CALL, flatten(params), channel="mcp", **attrs, metered=False)
            v = await layer.evaluate(ctx, {"server": server, "method": method})
            if v.blocked:
                return blocked(v)
            if v.action is Action.REDACT:
                cleaned = layer.redact_tree(params, principal, Direction.TOOL_CALL) if layer.spans_only(v) else None
                if cleaned is None:
                    return blocked(v)
                req = {**req, "params": cleaned}
            request_id = ctx.request_id
        elif not principal.authenticated and store.policy.identity.require_auth:
            return blocked(await layer.evaluate(Context(principal, Direction.TOOL_CALL, "", channel="mcp", **attrs)))

        try:
            resp = await mcp_forward(target, req)
        except (httpx.HTTPError, ValueError) as e:
            return rpc(rid, -32002, f"MCP server unavailable: {type(e).__name__}: {e}")
        result = resp.get("result")
        if call_ctx is not None and isinstance(result, dict) and not result.get("isError"):
            # Simulators, VMs, browsers: open, touch or close the lease this call stands for.
            args = req["params"].get("arguments") or {}
            layer.leases.after_call(call_ctx, store.policy, server, args, flatten(result))
        if not isinstance(result, dict) or method == "initialize":
            return JSONResponse(resp)

        if method == "tools/list":
            kept = []
            for tool in result.get("tools", []):
                if not isinstance(tool, dict):
                    continue
                # Name, description and every schema string: poisoning hides in parameter descriptions too.
                tctx = Context(
                    principal, Direction.TOOL_DESCRIPTION, flatten(tool), tool=tool.get("name"), channel="mcp", **attrs
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
            **attrs,
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
            **attribution(request, principal),
        )
        v = await layer.evaluate(ctx)
        return JSONResponse(_verdict_json(v), status_code=v.status_code if v.blocked else 200)

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

    governance.register(app, store, layer, app_reclaim, _api_key, _json_object)
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
