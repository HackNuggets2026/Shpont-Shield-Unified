"""HTTP gateway: OpenAI-compatible chat proxy, MCP proxy, guard API, admin API and dashboard."""

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse

from ..config import PolicyStore
from ..controls.access import authenticate
from ..decision import DecisionBackend
from ..engine import ControlLayer
from ..types import Action, Context, Direction, Verdict
from . import mcp_demo
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
            {"control": f.control, "category": f.category, "action": f.action.value, "proposed": f.proposed.value,
             "tier": f.tier, "score": round(f.score, 3), "detail": f.detail, "shadow": f.shadow}
            for f in v.findings
        ],
    }


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

    async def background() -> None:
        last_feed = time.monotonic()
        while True:
            await asyncio.sleep(1)
            store.poll()
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
    app.state.layer = layer
    app.state.store = store

    def upstream() -> UpstreamClient:
        return UpstreamClient(store.policy.upstream, http)

    # ---- agent/app -> model -------------------------------------------------

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        principal = authenticate(store.policy, _api_key(request))
        model = body.get("model")
        messages = body.get("messages", [])
        # Only the new turn is inspected; earlier turns were checked when they were sent.
        last_assistant = max((i for i, m in enumerate(messages) if m.get("role") == "assistant"), default=-1)
        new = [i for i in range(last_assistant + 1, len(messages)) if messages[i].get("role") in ("user", "tool")]
        if not new:
            new = [i for i, m in enumerate(messages) if m.get("role") == "user"][-1:]
        warnings: list[str] = []
        input_ctx = None
        for i in new:
            m = messages[i]
            direction = Direction.INPUT if m["role"] == "user" else Direction.TOOL_RESULT
            ctx = Context(principal, direction, _text(m.get("content")), model=model, channel="chat")
            v = await layer.evaluate(ctx)
            if v.blocked:
                return _policy_error(v)
            if v.action is Action.REDACT:
                messages[i] = {**m, "content": v.text}
            if v.action is Action.WARN:
                warnings.append(v.reason)
            if direction is Direction.INPUT:
                input_ctx = ctx
        if input_ctx is None:  # auth/budget/model gates still apply to tool-only turns
            input_ctx = Context(principal, Direction.INPUT, "", model=model, channel="chat")
            v = await layer.evaluate(input_ctx)
            if v.blocked:
                return _policy_error(v)

        try:
            completion = await upstream().chat({**body, "messages": messages})
        except httpx.HTTPError as e:
            return JSONResponse({"error": {"type": "upstream_error", "message": str(e)}}, status_code=502)
        cost = layer.ledger.record(
            input_ctx, layer.policy_for(principal.team), model or "unknown",
            completion.input_tokens, completion.output_tokens, completion.compute_seconds,
        )

        out_ctx = Context(principal, Direction.OUTPUT, completion.content, model=model, channel="chat",
                          request_id=input_ctx.request_id)
        ov = await layer.evaluate(out_ctx, {"usd": round(cost, 6), "tokens": completion.input_tokens + completion.output_tokens})
        content, finish = completion.content, "stop"
        if ov.blocked:
            content, finish = f"[Response withheld by policy: {ov.reason}]", "content_filter"
        elif ov.action is Action.REDACT:
            content = ov.text
        if ov.action is Action.WARN:
            warnings.append(ov.reason)

        resp = {
            "id": f"chatcmpl-{input_ctx.request_id}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": completion.input_tokens, "completion_tokens": completion.output_tokens,
                      "total_tokens": completion.input_tokens + completion.output_tokens},
            "control": {"input_request_id": input_ctx.request_id, "output_action": ov.action.value, "warnings": warnings},
        }
        headers = {"x-control-request-id": input_ctx.request_id, "x-control-action": ov.action.value}
        if body.get("stream"):
            # The full reply must be inspected before release, so it is sent as a single chunk.
            chunk = {**resp, "object": "chat.completion.chunk",
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": finish}]}
            payload = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"
            return StreamingResponse(iter([payload]), media_type="text/event-stream", headers=headers)
        return JSONResponse(resp, headers=headers)

    # ---- agent -> MCP tools -------------------------------------------------

    @app.post("/mcp/{server}")
    async def mcp(server: str, request: Request):
        req = await request.json()
        principal = authenticate(store.policy, _api_key(request))
        rid = req.get("id")
        target = store.policy.upstream.mcp_servers.get(server)
        if target is None:
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {"code": -32004, "message": f"unknown MCP server {server!r}"}})

        def rpc_error(v: Verdict) -> JSONResponse:
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32001, "message": f"blocked by policy: {v.reason}",
                "data": {"request_id": v.request_id, "action": v.action.value}}})

        async def forward(r: dict) -> dict:
            if target == "builtin":
                return mcp_demo.handle(r)
            resp = await http.post(target, json=r, headers={"accept": "application/json"})
            return resp.json()

        method = req.get("method")
        if method == "tools/call":
            params = req.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            ctx = Context(principal, Direction.TOOL_CALL, json.dumps(args), tool=name, tool_args=args, channel="mcp")
            v = await layer.evaluate(ctx, {"server": server})
            if v.blocked:
                return rpc_error(v)
            if v.action is Action.REDACT:
                try:
                    req = {**req, "params": {**params, "arguments": json.loads(v.text)}}
                except json.JSONDecodeError:
                    req = {**req, "params": {**params, "arguments": {"redacted": v.text}}}
            resp = await forward(req)
            result = resp.get("result")
            if not result:
                return JSONResponse(resp)
            texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
            rctx = Context(principal, Direction.TOOL_RESULT, "\n".join(texts), tool=name, channel="mcp",
                           request_id=ctx.request_id)
            rv = await layer.evaluate(rctx, {"server": server})
            if rv.blocked:
                return rpc_error(rv)
            if rv.action is Action.REDACT:
                result = {**result, "content": [{"type": "text", "text": rv.text}]}
            return JSONResponse({**resp, "result": result})

        if not principal.authenticated and store.policy.identity.require_auth:
            ctx = Context(principal, Direction.TOOL_CALL, "", channel="mcp")
            return rpc_error(await layer.evaluate(ctx))
        resp = await forward(req)
        if method == "tools/list" and "result" in resp:
            kept = []
            for tool in resp["result"].get("tools", []):
                ctx = Context(principal, Direction.TOOL_DESCRIPTION, tool.get("description", ""),
                              tool=tool.get("name"), channel="mcp")
                v = await layer.evaluate(ctx, {"server": server})
                if not v.blocked:
                    kept.append(tool)
            resp = {**resp, "result": {**resp["result"], "tools": kept}}
        return JSONResponse(resp)

    # ---- SDK / sidecar check --------------------------------------------------

    @app.post("/v1/guard")
    async def guard(request: Request):
        body = await request.json()
        principal = authenticate(store.policy, _api_key(request))
        try:
            direction = Direction(body.get("direction", "input"))
        except ValueError:
            return JSONResponse({"error": f"direction must be one of {[d.value for d in Direction]}"}, status_code=400)
        ctx = Context(principal, direction, _text(body.get("text", "")), model=body.get("model"),
                      tool=body.get("tool"), channel="sdk")
        v = await layer.evaluate(ctx)
        return JSONResponse(_verdict_json(v), status_code=v.status_code if v.blocked else 200)

    # ---- reporting ------------------------------------------------------------

    @app.get("/admin/summary")
    async def summary():
        a = layer.audit
        p = store.policy
        controls = [
            {"name": n, "kind": "deterministic", "enabled": c.enabled, "mode": c.mode.value, "shadow": c.shadow,
             "hits": a.controls.get(n, 0)}
            for n, c in (("secrets", p.secrets), ("pii", p.pii), ("signatures", p.signatures), ("tool_access", p.tool_access))
        ] + [
            {"name": n, "kind": "semantic", "enabled": c.enabled, "mode": c.mode.value, "shadow": c.shadow,
             "hits": a.controls.get(n, 0)}
            for n, c in p.semantic_controls.items()
        ]
        return {
            "policy": {"name": p.name, "version": p.version, "reloads": store.reloads, "last_error": store.last_error},
            "feed": {"version": layer.feed.feed_version, "signatures": len(layer.feed.signatures),
                     "loaded_at": layer.feed.loaded_at, "errors": layer.feed.errors},
            "semantic": {"backend": p.semantic.backend, "fast_model": p.semantic.fast_model,
                         "deep_model": p.semantic.deep_model, "fail_mode": p.semantic.fail_mode},
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
        out = [e for e in reversed(layer.audit.events)
               if (not action or e["action"] == action)
               and (not control or any(f["control"] == control for f in e["findings"]))]
        return out[:limit]

    @app.get("/admin/audit/export")
    async def export(format: str = "jsonl"):
        rows = list(layer.audit.events)
        if format == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["ts", "request_id", "principal", "team", "channel", "direction", "model", "tool",
                        "action", "status_code", "reason", "controls", "policy_version"])
            for e in rows:
                w.writerow([e["ts"], e["request_id"], e["principal"], e["team"], e["channel"], e["direction"],
                            e["model"], e["tool"], e["action"], e["status_code"], e["reason"],
                            ";".join(f"{f['control']}/{f['category']}" for f in e["findings"]), e["policy_version"]])
            return Response(buf.getvalue(), media_type="text/csv",
                            headers={"content-disposition": "attachment; filename=audit.csv"})
        return Response("".join(json.dumps(e) + "\n" for e in rows), media_type="application/x-ndjson",
                        headers={"content-disposition": "attachment; filename=audit.jsonl"})

    @app.get("/admin/policy")
    async def policy_view():
        p = store.policy
        return {"version": p.version, "reloads": store.reloads, "last_error": store.last_error,
                "policy": p.model_dump(mode="json", by_alias=True)}

    @app.post("/admin/policy/reload")
    async def policy_reload():
        ok = store.reload()
        return JSONResponse({"ok": ok, "version": store.policy.version, "error": store.last_error},
                            status_code=200 if ok else 422)

    @app.get("/metrics")
    async def metrics():
        a = layer.audit
        lines = ["# TYPE acl_decisions_total counter"]
        lines += [f'acl_decisions_total{{action="{k}"}} {v}' for k, v in a.actions.items()]
        lines += ["# TYPE acl_findings_total counter"]
        lines += [f'acl_findings_total{{control="{k}"}} {v}' for k, v in a.controls.items()]
        lines += ["# TYPE acl_latency_ms summary"]
        for stage, s in a.latency_summary().items():
            lines += [f'acl_latency_ms{{stage="{stage}",quantile="0.5"}} {s["p50"]}',
                      f'acl_latency_ms{{stage="{stage}",quantile="0.95"}} {s["p95"]}']
        snap = layer.ledger.snapshot(store.policy)
        lines += ["# TYPE acl_budget_usd gauge"]
        lines += [f'acl_budget_usd{{scope="{r["scope"]}",key="{r["key"]}"}} {r["usd"]}' for r in snap["scopes"]]
        return PlainTextResponse("\n".join(lines) + "\n")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        return DASHBOARD.read_text() if DASHBOARD.exists() else "<p>dashboard not built</p>"

    return app


def _text(content: Any) -> str:
    """OpenAI content is a string or a list of parts; only text parts are inspected."""
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return "" if content is None else str(content)
