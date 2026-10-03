"""Security console API: spend and risk analytics, and grants made by security for an agent's owner.

Everything here is under /admin, behind the admin token.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from .. import resources
from ..analytics import BANDS, LEVEL_RANK, SORTS, Analytics
from ..config import PolicyStore

RISK_FILTERS = ("", "flagged", "elevated", *LEVEL_RANK)
BUDGET_FILTERS = ("", "nocap", *(b for b, _ in BANDS))
Period = Query(30, ge=1, le=31, description="days, ending with `end`")
End = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="last day (UTC), default today")


def mount(app: FastAPI, layer: Any, store: PolicyStore) -> Analytics:
    analytics = Analytics(layer, store.base_dir / "data" / "history.json")
    app.state.analytics = analytics

    def bad(message: str) -> JSONResponse:
        return JSONResponse({"error": message}, status_code=400)

    async def body_of(request: Request) -> dict[str, Any] | None:
        try:
            body = await request.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    @app.get("/admin/analytics/overview")
    async def overview(period: int = Period, end: str | None = End):
        try:
            return analytics.overview(period, end)
        except ValueError as e:
            return bad(str(e))

    @app.get("/admin/analytics/people")
    async def people(
        q: str = "",
        team: str = "",
        risk: str = "",
        budget: str = "",
        item: str = "",
        kind: str = "human",
        min_usd: float | None = Query(None, ge=0, allow_inf_nan=False),
        max_usd: float | None = Query(None, ge=0, allow_inf_nan=False),
        sort: str = "-usd",
        page: int = Query(1, ge=1),
        per_page: int = Query(50, ge=1, le=200),
        period: int = Period,
        end: str | None = End,
    ):
        for name, value, allowed in (
            ("risk", risk, RISK_FILTERS),
            ("budget", budget, BUDGET_FILTERS),
            ("kind", kind, ("human", "agent")),
            ("sort", sort.removeprefix("-"), tuple(SORTS)),
        ):
            if value not in allowed:
                return bad(f"{name} must be one of {[a for a in allowed if a]}")
        try:
            return analytics.people_page(
                q, team, risk, budget, item, kind, min_usd, max_usd, sort, page, per_page, period, end
            )
        except ValueError as e:
            return bad(str(e))

    @app.get("/admin/analytics/person/{pid}")
    async def person(pid: str, period: int = Period, end: str | None = End):
        try:
            out = analytics.person(pid, period, end)
        except ValueError as e:
            return bad(str(e))
        if out is None:
            return JSONResponse({"error": f"unknown principal {pid!r}"}, status_code=404)
        return out

    @app.get("/admin/analytics/resources")
    async def resource_usage(period: int = Period, end: str | None = End):
        try:
            return analytics.resources(period, end)
        except ValueError as e:
            return bad(str(e))

    def owner_of(agent: str) -> Any:
        a = analytics.people().get(agent)
        if a is None or a.kind != "agent":
            return None
        return analytics.people()[a.owner].principal  # type: ignore[index]

    @app.post("/admin/grants")
    async def grant(request: Request):
        """Grant a resource to an agent as its owner could: the owner's entitlement still applies."""
        body = await body_of(request)
        if body is None:
            return bad("expected a JSON object")
        agent, rid, hours = str(body.get("agent")), str(body.get("resource")), body.get("hours")
        owner = owner_of(agent)
        if owner is None:
            return JSONResponse({"error": f"unknown agent {agent!r}"}, status_code=404)
        try:
            g = resources.grant(
                store.policy,
                layer.state,
                owner,
                agent,
                rid,
                [str(x) for x in body.get("scopes") or []],
                float(hours) if hours is not None else None,
            )
        except (resources.GrantError, ValueError, TypeError) as e:
            return bad(str(e))
        layer.audit.note("grant", "security", agent=agent, resource=rid, owner=owner.id, grant=g)
        return g

    @app.patch("/admin/grants/{agent}/{rid}")
    async def grant_scopes(agent: str, rid: str, request: Request):
        body = await body_of(request)
        if body is None:
            return bad("expected a JSON object")
        owner = owner_of(agent)
        if owner is None:
            return JSONResponse({"error": f"unknown agent {agent!r}"}, status_code=404)
        before = set((layer.state.grants.get(agent, {}).get(rid) or {}).get("scopes", []))
        try:
            g = resources.set_scopes(
                store.policy, layer.state, owner, agent, rid, [str(x) for x in body.get("scopes") or []]
            )
        except resources.GrantError as e:
            return bad(str(e))
        layer.audit.note(
            "grant_scopes",
            "security",
            agent=agent,
            resource=rid,
            scopes=g["scopes"],
            added=sorted(set(g["scopes"]) - before),
            removed=sorted(before - set(g["scopes"])),
        )
        return g

    return analytics
