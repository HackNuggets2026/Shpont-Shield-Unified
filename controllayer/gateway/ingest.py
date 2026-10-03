"""Generic event ingest: CloudEvents 1.0 from any producer (CI, cloud brokers, internal agents).

POST /v1/events   one structured CloudEvent (application/cloudevents+json) or a batch
                  (application/cloudevents-batch+json, a JSON array). With an employee key every event is
                  attributed to that employee; with the admin token `subject` names the principal.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..config import PolicyStore
from ..controls.access import authenticate
from ..engine import ControlLayer
from ..events import from_cloudevent

MAX_BATCH = 1000


def register(app: FastAPI, store: PolicyStore, layer: ControlLayer, api_key: Callable[[Request], str | None]) -> None:
    def err(msg: str, code: int = 400) -> JSONResponse:
        return JSONResponse({"error": msg}, status_code=code)

    @app.post("/v1/events")
    async def v1_events(request: Request):
        p = store.policy
        who = authenticate(p, api_key(request))
        token = p.identity.admin_token
        admin = bool(token) and hmac.compare_digest(request.headers.get("x-admin-token") or "", token)
        if not who.authenticated and not admin:
            return err("API key or admin token required", 401)
        try:
            body = json.loads(await request.body() or b"null")
        except ValueError:
            return err("body must be JSON")
        batch = body if isinstance(body, list) else [body]
        if not batch or len(batch) > MAX_BATCH or not all(isinstance(x, dict) for x in batch):
            return err(f"send one CloudEvent object or a list of 1-{MAX_BATCH}")
        try:
            events = [from_cloudevent(ce) for ce in batch]
        except (ValueError, TypeError) as e:
            return err(f"invalid CloudEvent: {e}")
        ids, duplicates = [], 0
        for e in events:
            if who.authenticated:
                e.update(principal=who.id, team=who.team, email=None)  # an employee reports only their own usage
            row = layer.ingestor.ingest(e, p, dedupe=True)
            duplicates += bool(row.get("duplicate"))
            ids.append(row["id"])
        return {"ok": True, "accepted": len(ids), "ids": ids, "duplicates": duplicates}
