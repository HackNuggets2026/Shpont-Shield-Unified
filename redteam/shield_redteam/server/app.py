"""The Redteam sidecar: attacks the gateway continuously (Poligon) and reports the posture.

It runs as its own process next to the gateway, which proxies it to the console under
/api/admin/redteam/*. It never edits the gateway's configuration: controls are changed in the console.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from ..config import Config
from ..core.target import GatewayTarget, Target
from ..poligon import corpus
from ..poligon.feedcheck import check_feed
from ..poligon.service import Poligon
from .report import markdown
from .stage import outcome_rows, rings

EMPTY_FEED: dict[str, Any] = {"feed_version": "none", "signatures": []}


def load_feed(cfg: Config) -> tuple[dict[str, Any], str]:
    """The signature feed the gateway consumes (a file or URL), so `feed --check` tests what is enforced."""
    loc = cfg.feed
    if not loc:
        return EMPTY_FEED, "no feed configured (feed: in redteam.yaml)"
    try:
        if str(loc).startswith(("http://", "https://")):
            return httpx.get(loc, timeout=5).json(), ""
        return json.loads(cfg.resolve(loc).read_text()), ""
    except Exception as e:  # noqa: BLE001 - shown on the page and by `feed --check`
        return EMPTY_FEED, f"feed {loc!r} unavailable: {type(e).__name__}: {e}"


def create_app(cfg: Config, target: Target | None = None, background: bool = True) -> FastAPI:
    target = target or GatewayTarget(cfg.target["url"], cfg.target["admin_token"])
    pol = cfg.poligon
    poligon = Poligon(
        target,
        corpus.load_dir(cfg.resolve(pol["corpus"])),
        mutate=bool(pol["mutate"]),
        interval=float(pol["interval_seconds"]),
        workers=int(pol["workers"]),
    )
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            with contextlib.suppress(Exception):
                poligon.tick()
            stop.wait(1.0)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        thread = threading.Thread(target=loop, daemon=True) if background else None
        if thread:
            thread.start()
        yield
        stop.set()

    app = FastAPI(title="Shpont Shield Redteam", lifespan=lifespan)
    app.state.poligon = poligon

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        """A POST here fires the corpus at the gateway, so only JSON from this origin (or the proxy) is taken.

        Requiring application/json makes a cross-site browser request trigger a CORS preflight, which
        this server never answers; a present Origin must also be this server's own.
        """
        if request.method == "POST":
            ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype != "application/json":
                return JSONResponse({"error": "send JSON (content-type: application/json)"}, status_code=415)
            origin = request.headers.get("origin")
            if origin and origin.split("://", 1)[-1] != request.headers.get("host", ""):
                return JSONResponse({"error": "cross-origin request refused"}, status_code=403)
        return await call_next(request)

    def feedcheck() -> dict[str, Any]:
        feed, err = load_feed(cfg)
        return {**check_feed(feed), "error": err}

    def describe() -> dict[str, Any]:
        try:
            return target.describe()
        except Exception as e:  # noqa: BLE001 - an unreachable gateway is a state to show
            return {"name": getattr(target, "name", "?"), "error": f"{type(e).__name__}: {e}"}

    @app.get("/api/state")
    def state():
        info = describe()
        return {
            "now": time.time(),
            "target": {k: v for k, v in info.items() if k != "policy_doc"},
            "poligon": poligon.state(),
            "feedcheck": feedcheck(),
        }

    @app.get("/api/stage")
    def stage():
        """The control layer as rings of defence, and where each test payload ended."""
        info = describe()
        ring_list = rings(info)
        st = poligon.state()
        return {
            "now": time.time(),
            "target": {
                "name": info.get("name"),
                "error": info.get("error") or st["error"],
                "policy": (info.get("policy") or {}).get("version"),
                "feed": (info.get("feed") or {}).get("version"),
                "semantic": (info.get("semantic") or {}).get("backend"),
            },
            "rings": ring_list,
            "run": st["runs"],
            "outcomes": outcome_rows(poligon.last.outcomes, ring_list) if poligon.last else [],
        }

    @app.post("/api/run")
    def run_now():
        return poligon.run_now()

    @app.get("/api/report")
    @app.get("/api/report.json")
    def report_json():
        return {"poligon": poligon.state(), "feedcheck": feedcheck()}

    @app.get("/api/report.md", response_class=PlainTextResponse)
    def report_md():
        return markdown(poligon.state(), feedcheck())

    return app
