"""Proxy to the Redteam sidecar (redteam/, `shield-redteam serve`), behind the admin guard.

The sidecar is a separate process that attacks this gateway through /admin/try and scores its
posture. The console reads it as /api/admin/redteam/<path>, forwarded to <sidecar>/api/<path>.
"""

from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

DEFAULT_URL = "http://127.0.0.1:8799"
START = "cd redteam && ../.venv/bin/python -m shield_redteam serve   (or: make redteam)"


def sidecar_url() -> str:
    return (os.environ.get("ACL_REDTEAM_URL") or DEFAULT_URL).rstrip("/")


def register(app: FastAPI) -> None:
    # Tests put an httpx.ASGITransport here to reach an in-process sidecar.
    app.state.redteam_transport = None

    @app.api_route("/admin/redteam/{path:path}", methods=["GET", "POST"], include_in_schema=False)
    async def redteam_proxy(path: str, request: Request):
        base = sidecar_url()
        # A run fires the whole corpus at this gateway; reads answer at once.
        timeout = 180.0 if request.method == "POST" else 10.0
        transport = app.state.redteam_transport
        try:
            async with httpx.AsyncClient(base_url=base, timeout=timeout, transport=transport) as client:
                r = await client.request(
                    request.method,
                    f"/api/{path}",
                    params=[(k, v) for k, v in request.query_params.multi_items() if k != "token"],
                    content=await request.body() if request.method == "POST" else None,
                    headers={"content-type": "application/json"} if request.method == "POST" else None,
                )
        except httpx.HTTPError as e:
            return JSONResponse(
                {"error": "redteam not running", "url": base, "detail": f"{type(e).__name__}", "start": START},
                status_code=503,
            )
        return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))
