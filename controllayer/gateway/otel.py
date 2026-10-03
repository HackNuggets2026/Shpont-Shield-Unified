"""OTLP/HTTP JSON receiver for Claude Code telemetry.

  POST /v1/logs      events: api_request (cost, tokens), tool_decision, tool_result, api_error, user_prompt...
  POST /v1/metrics   value metrics: lines of code, commits, pull requests, active time, edit decisions

Point Claude Code at the gateway with OTEL_EXPORTER_OTLP_PROTOCOL=http/json,
OTEL_EXPORTER_OTLP_ENDPOINT=http://gateway:8787 and OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer <key>".
With an employee key, everything is that employee's; with only the admin token (a central collector),
people are matched by `user.email` against `identity.api_keys.*.email`.
"""

from __future__ import annotations

import gzip
import hmac
import json
import logging
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .. import claude_code
from ..config import PolicyStore
from ..controls.access import authenticate
from ..engine import ControlLayer

log = logging.getLogger(__name__)


def register(app: FastAPI, store: PolicyStore, layer: ControlLayer, api_key: Callable[[Request], str | None]) -> None:
    cumulative: dict[tuple, float] = {}

    async def receive(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None, Any]:
        p = store.policy
        who = authenticate(p, api_key(request))
        token = p.identity.admin_token
        admin = bool(token) and hmac.compare_digest(request.headers.get("x-admin-token") or "", token)
        if not who.authenticated and not admin:
            return None, JSONResponse({"error": "API key or admin token required"}, status_code=401), None
        if "protobuf" in request.headers.get("content-type", ""):
            msg = "send OTLP as JSON: OTEL_EXPORTER_OTLP_PROTOCOL=http/json"
            return None, JSONResponse({"error": msg}, status_code=415), None
        raw = await request.body()
        if request.headers.get("content-encoding", "").lower() == "gzip" or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return None, JSONResponse({"error": "body must be OTLP JSON"}, status_code=400), None
        if not isinstance(body, dict):
            return None, JSONResponse({"error": "body must be OTLP JSON"}, status_code=400), None
        return body, None, who if who.authenticated else None

    def ingest(events: list[dict[str, Any]], who) -> int:
        p = store.policy
        for e in events:
            if who is not None:  # the key's owner, whatever the telemetry claims
                e.update(principal=who.id, team=who.team, email=None)
            try:
                row = layer.ingestor.ingest(e, p)
                if row.get("session") and row.get("workflow"):  # hooks in the same session inherit the label
                    layer.sessions[(row["principal"], row["session"])] = row["workflow"]
            except Exception:  # noqa: BLE001 - one odd record must not drop the rest of the batch
                log.exception("telemetry event rejected: %s", e.get("kind"))
        return len(events)

    @app.post("/v1/logs")
    async def otlp_logs(request: Request):
        body, error, who = await receive(request)
        if error:
            return error
        ingest(claude_code.log_events(body), who)
        return {"partialSuccess": {}}

    @app.post("/v1/metrics")
    async def otlp_metrics(request: Request):
        body, error, who = await receive(request)
        if error:
            return error
        ingest(claude_code.metric_events(body, cumulative), who)
        return {"partialSuccess": {}}
