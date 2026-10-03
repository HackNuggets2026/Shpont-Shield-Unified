"""Resource catalog API: the catalog, time-boxed grants, a Cedar-style authorization endpoint, and
FinOps FOCUS / Backstage exports.

  GET  /admin/catalog                       every resource by class, with 30-day usage, live leases and grants
  GET  /admin/grants                        every grant (live and expired)
  POST /admin/principals/{pid}/grants       grant access {resource, minutes, actions?, workflow?, reason}
  POST /admin/principals/{pid}/grants/{id}/revoke   {reason}
  GET  /admin/export/focus?days=30          AI spend as a FOCUS 1.1 CSV
  GET  /admin/export/backstage              the catalog as Backstage `kind: Resource` entities
  POST /v1/import/focus                     a cloud bill (FOCUS CSV) into usage; admin token
  POST /v1/authorize                        may this principal do this action on this resource now?
  GET  /me/grants                           the caller's grants and the resources they can request
"""

from __future__ import annotations

import hmac
import math
import time
import uuid
from collections.abc import Awaitable, Callable
from fnmatch import fnmatch
from typing import Any

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from .. import focus
from ..config import Grant, Policy, PolicyStore
from ..controls.access import authenticate
from ..controls.resources import authorize, resolve
from ..engine import ControlLayer
from ..types import Context, Direction, Principal

DAY = 86400.0
CLASSES = ("consumable", "leasable", "access_grant")


def grant_event(usage, pid: str, g: dict[str, Any], by: str) -> None:
    """A grant in the activity feed."""
    minutes = round(((g.get("expires") or 0) - (g.get("granted_at") or 0)) / 60, 1) if g.get("expires") else None
    usage.add_event({"ts": g.get("granted_at"), "source": "admin", "kind": "grant", "principal": pid,
                     "resource": g["resource"], "workflow": g.get("workflow"), "decision": "approved",
                     "severity": "low", "detail": {"grant": g["id"], "minutes": minutes, "by": by}})  # fmt: skip


def register(
    app: FastAPI,
    store: PolicyStore,
    layer: ControlLayer,
    api_key: Callable[[Request], str | None],
    json_object: Callable[[Request], Awaitable[dict[str, Any]]],
) -> None:
    usage = layer.usage

    def err(msg: str, code: int = 400) -> JSONResponse:
        return JSONResponse({"error": msg}, status_code=code)

    def actor(request: Request) -> str:
        return (request.headers.get("x-admin-user") or "admin")[:80]

    def is_admin(request: Request) -> bool:
        token = store.policy.identity.admin_token
        given = request.headers.get("x-admin-token") or ""
        return not token or hmac.compare_digest(given, token)

    def known(policy: Policy) -> dict[str, Principal]:
        return {k.principal: Principal(k.principal, k.team, k.role) for k in policy.identity.api_keys.values()}

    def grant_view(pid: str, g: Grant, policy: Policy, now: float) -> dict[str, Any]:
        r = policy.catalog.get(g.resource)
        return {
            "principal": pid,
            **g.model_dump(),
            "title": r.title if r else g.resource,
            "live": g.live(now),
            "minutes_left": round((g.expires - now) / 60, 1) if g.expires and g.live(now) else None,
        }

    def all_grants(policy: Policy, now: float) -> list[dict[str, Any]]:
        rows = [grant_view(pid, g, policy, now) for pid, pp in policy.principals.items() for g in pp.grants]
        return sorted(rows, key=lambda r: (not r["live"], -(r["granted_at"] or 0)))

    def new_grant(policy: Policy, pid: str, body: dict, by: str, reason: str) -> list[dict] | str:
        """The principal's grant list with one more grant, or why it cannot be granted."""
        name = resolve(policy, str(body.get("resource") or ""))
        if name is None:
            return f"unknown resource {body.get('resource')!r}"
        r = policy.catalog[name]
        if r.class_ != "access_grant":
            return f"{name} is {r.class_}; only access_grant resources are granted"
        try:
            minutes = float(body.get("minutes") or r.grant.max_minutes)
        except (TypeError, ValueError):
            return "minutes must be a number"
        if not math.isfinite(minutes) or minutes <= 0:
            return "minutes must be a positive number"
        minutes = min(minutes, r.grant.max_minutes)
        raw = body.get("actions") or []
        actions = (
            [raw] if isinstance(raw, str) else [a for a in raw if isinstance(a, str)] if isinstance(raw, list) else []
        )
        if r.actions and set(actions) - set(r.actions):
            return f"{name} supports actions {r.actions}"
        wf = body.get("workflow") or None
        if wf is not None and wf not in policy.menu.workflows:
            return f"unknown workflow {wf!r}"
        now = time.time()
        g = Grant(
            id=uuid.uuid4().hex[:10],
            resource=name,
            locations=[r.urn],
            actions=actions or list(r.actions),
            workflow=wf,
            expires=now + minutes * 60,
            granted_by=by,
            reason=reason,
            granted_at=now,
        )
        # Expired grants stay a week for the record, then drop out of the overlay.
        keep = [x for x in policy.principal(pid).grants if x.live(now - 7 * DAY)]
        return [x.model_dump() for x in [*keep, g]]

    def workflows_using(policy: Policy, name: str, r: Any) -> dict[str, str]:
        """Workflows that use a resource, and how: they list it, their model globs cover it, or their tool
        globs cover its tools. A workflow with no model or tool limit is not counted as using everything."""

        def overlap(mine: list[str], theirs: list[str]) -> bool:
            return any(fnmatch(a, b) or fnmatch(b, a) for a in mine for b in theirs)

        tools = list(r.tools)
        if r.lease:  # a workflow that can only keep someone else's lease busy does not use the resource
            tools += r.lease.start_tools
        out: dict[str, str] = {}
        for w, wf in policy.menu.workflows.items():
            if name in wf.resources:
                out[w] = "resources"
            elif r.models and wf.models and overlap(r.models, wf.models):
                out[w] = "models"
            elif tools and wf.tools and overlap(tools, wf.tools):
                out[w] = "tools"
        return out

    def write(request: Request, patch: dict, action: str, target: str, reason: str, by: str | None = None):
        try:
            p = store.write_overlay(patch)
        except ValueError as e:
            return err(f"rejected: {e}", 422)
        usage.log_admin(by or actor(request), action, target, reason, patch)
        return JSONResponse({"ok": True, "version": p.version})

    app.state.grant_for_request = new_grant  # used by approvals in governance.py

    # ---- catalog -----------------------------------------------------------------------

    @app.get("/admin/catalog")
    async def admin_catalog(days: int = 30):
        p, now = store.policy, time.time()
        since = now - max(1, min(days, 365)) * DAY
        used = {r["resource"]: r for r in usage.breakdown(["resource"], since)}
        leases = layer.leases.snapshot(p)
        grants = all_grants(p, now)
        rows = []
        for name, r in p.catalog.items():
            u = used.get(name, {})
            via = workflows_using(p, name, r)
            rows.append(
                {
                    "name": name,
                    "class": r.class_,
                    **r.model_dump(exclude={"class_"}, exclude_none=True),
                    "usage": {
                        "usd": round(u.get("usd") or 0, 6),
                        "tokens": u.get("tokens") or 0,
                        "minutes": round(u.get("minutes") or 0, 1),
                        "requests": u.get("requests") or 0,
                    },
                    "live_leases": sum(1 for x in leases if x["resource"] == name),
                    "live_grants": sum(1 for g in grants if g["resource"] == name and g["live"]),
                    "workflows": list(via),
                    "workflows_via": via,
                }
            )
        return {
            "classes": {c: [x for x in rows if x["class"] == c] for c in CLASSES},
            "uncatalogued": [
                {"resource": k, "usd": round(v.get("usd") or 0, 6)}
                for k, v in used.items()
                if k not in p.catalog and k not in ("guard",)
            ],
        }

    def with_unit(g: dict[str, Any]) -> dict[str, Any]:
        person = usage.person(g["principal"]) or {}
        team = person.get("team")
        return {**g, "department": usage.department_of(g["principal"], team), "team": team,
                "name": person.get("name") or g["principal"]}  # fmt: skip

    @app.get("/admin/grants")
    async def admin_grants(
        live: bool = False,
        envelope: bool = False,
        limit: int = 200,
        offset: int = 0,
        department: str | None = None,
        resource: str | None = None,
    ):
        """Every grant, live first. The plain array keeps its shape; `envelope=1` adds paging, filters, a total
        and a summary: {total, grants, summary: {live, expiring_1h, by_resource}}."""
        now = time.time()
        rows = [with_unit(g) for g in all_grants(store.policy, now)]
        if not envelope:
            return [g for g in rows if g["live"]] if live else rows
        live_rows = [g for g in rows if g["live"]]
        by_resource: dict[str, int] = {}
        for g in live_rows:
            by_resource[g["resource"]] = by_resource.get(g["resource"], 0) + 1
        shown = [g for g in rows if (not live or g["live"]) and (not department or g["department"] == department)
                 and (not resource or g["resource"] == resource)]  # fmt: skip
        limit, offset = max(1, min(limit, 1000)), max(0, offset)
        return {
            "total": len(shown),
            "grants": shown[offset : offset + limit],
            "summary": {
                "live": len(live_rows),
                "expiring_1h": sum(1 for g in live_rows if g["expires"] is not None and g["expires"] - now <= 3600),
                "by_resource": [
                    {"resource": k, "live": v} for k, v in sorted(by_resource.items(), key=lambda kv: -kv[1])
                ],
            },
        }

    @app.post("/admin/principals/{pid}/grants")
    async def admin_grant(pid: str, request: Request):
        body = await json_object(request)
        reason = str(body.get("reason") or "").strip()
        if not reason:
            return err("a reason is required; it is shown to the employee")
        p = store.policy
        if (
            pid not in known(p)
            and pid not in p.principals
            and usage.person(pid) is None
            and not usage.breakdown(["principal"], 0, pid)
        ):
            return err(f"no such person {pid!r}", 404)
        grants = new_grant(p, pid, body, actor(request), reason)
        if isinstance(grants, str):
            return err(grants)
        resp = write(request, {"principals": {pid: {"grants": grants}}}, "grant", pid, reason)
        if resp.status_code != 200:
            return resp
        g = grants[-1]
        grant_event(usage, pid, g, actor(request))
        out: dict[str, Any] = {"ok": True, "grant": g}
        asked = body.get("minutes")
        granted = (g["expires"] - g["granted_at"]) / 60
        if asked and float(asked) > granted + 1e-6:  # new_grant validated it as a number
            out["clamped_from"] = float(asked)
        return JSONResponse(out)

    @app.post("/admin/principals/{pid}/grants/{gid}/revoke")
    async def admin_revoke(pid: str, gid: str, request: Request):
        body = await json_object(request)
        reason = str(body.get("reason") or "").strip()
        if not reason:
            return err("a reason is required; it is shown to the employee")
        p, now = store.policy, time.time()
        grants = p.principal(pid).grants
        if not any(g.id == gid and g.live(now) for g in grants):
            return err("no such live grant", 404)
        kept = [g.model_dump() | ({"expires": now} if g.id == gid else {}) for g in grants]
        return write(request, {"principals": {pid: {"grants": kept}}}, "revoke_grant", pid, reason)

    # ---- authorization (Cedar-shaped: principal, action, resource, context) -------------

    @app.post("/v1/authorize")
    async def v1_authorize(request: Request):
        """Ask before acting: other systems (CI, cloud brokers, agents) check a decision without doing anything.

        Body: {"action": "start", "resource": "<name or URN>", "context": {"workflow": "ui_qa"}}.
        Authenticated with the principal's own key, or the admin token plus "principal"."""
        body = await json_object(request)
        p = store.policy
        who = authenticate(p, api_key(request))
        if not who.authenticated and body.get("principal") and is_admin(request):
            who = known(p).get(str(body["principal"]), who)
        if not who.authenticated:
            return err("API key (or admin token and principal) required", 401)
        name = resolve(p, str(body.get("resource") or ""))
        if name is None:
            return err(f"unknown resource {body.get('resource')!r}", 404)
        context = body.get("context") if isinstance(body.get("context"), dict) else {}
        action = str(body.get("action") or "use")
        held = len(layer.leases.held(who.id, name))
        d = authorize(p, who, action, name, context.get("workflow"), ledger=layer.ledger, held=held)
        return {
            "decision": "Allow" if d.allow else "Deny",
            "principal": {"type": "Shield::User", "id": who.id, "team": who.team, "role": who.role},
            **d.to_dict(),
        }

    # ---- FinOps FOCUS and Backstage ------------------------------------------------------

    @app.get("/admin/export/focus")
    async def export_focus(days: int = 30):
        since = time.time() - max(1, min(days, 400)) * DAY
        text = focus.export_csv(store.policy, usage.cost_rows(since))
        return Response(text, media_type="text/csv", headers={"content-disposition": "attachment; filename=focus.csv"})

    @app.post("/v1/import/focus")
    async def import_focus(request: Request):
        """A FOCUS cost export (AWS CUR 2.0 FOCUS, Azure, GCP) into the usage ledger. Admin token required."""
        if not is_admin(request) or not store.policy.identity.admin_token:
            return err("admin token required", 401)
        raw = (await request.body()).decode("utf-8", "replace")
        p = store.policy
        try:
            rows = focus.parse_csv(p, raw, request.query_params.get("resource") or "cloud")
        except ValueError as e:
            return err(str(e))
        for r in rows:
            usage.add(**r, metered=True, source="billing_export")
            layer.ledger.absorb(r["principal"], r["team"], r["usd"], r["ts"])
        usage.log_admin(actor(request), "import_focus", "billing", f"{len(rows)} rows", None)
        return {"ok": True, "rows": len(rows), "usd": round(sum(r["usd"] for r in rows), 6)}

    @app.get("/admin/export/backstage")
    async def export_backstage():
        docs = []
        for name, r in store.policy.catalog.items():
            docs.append(
                {
                    "apiVersion": "backstage.io/v1alpha1",
                    "kind": "Resource",
                    "metadata": {
                        "name": name.replace("_", "-"),
                        "title": r.title or name,
                        "description": r.description,
                        "annotations": {"shpont.dev/urn": r.urn, "shpont.dev/class": r.class_},
                        "tags": [r.category, r.class_.replace("_", "-")],
                    },
                    "spec": {"type": r.category, "owner": r.owner or "unknown", "system": "ai-control-layer"},
                }
            )
        return PlainTextResponse(yaml.safe_dump_all(docs, sort_keys=False), media_type="application/yaml")

    # ---- employee -----------------------------------------------------------------------

    @app.get("/me/grants")
    async def me_grants(request: Request):
        p = store.policy
        who = authenticate(p, api_key(request) or request.query_params.get("key"))
        if not who.authenticated:
            return err("your API key is required", 401)
        now = time.time()
        return {
            "grants": [grant_view(who.id, g, p, now) for g in p.principal(who.id).grants],
            "requestable": [
                {
                    "name": n,
                    "title": r.title or n,
                    "urn": r.urn,
                    "actions": r.actions,
                    "sensitivity": r.sensitivity,
                    "approval": r.grant.approval if r.grant else "admin",
                    "max_minutes": r.grant.max_minutes if r.grant else None,
                }
                for n, r in p.catalog.items()
                if r.class_ == "access_grant"
            ],
        }

    # ---- RateLimit headers ----------------------------------------------------------------

    @app.middleware("http")
    async def ratelimit_headers(request: Request, call_next):
        resp = await call_next(request)
        path = request.url.path
        if not (path == "/v1/chat/completions" or path.startswith("/mcp/")):
            return resp
        p = store.policy
        who = authenticate(p, api_key(request))
        if not who.authenticated or not p.budgets.enabled:
            return resp
        quotas = layer.ledger.quota(Context(who, Direction.INPUT, ""), p)
        if quotas:
            tight = min(quotas, key=lambda q: q["remaining"] / max(q["limit"], 1))
            name = f"{tight['scope'].replace(':', '-')}-{tight['unit']}"
            t = time.gmtime()
            reset = 86400 - (t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec)
            resp.headers["RateLimit-Policy"] = f'"{name}";q={tight["limit"]};w=86400;qu="{tight["unit"]}"'
            resp.headers["RateLimit"] = f'"{name}";r={tight["remaining"]};t={reset}'
        return resp
