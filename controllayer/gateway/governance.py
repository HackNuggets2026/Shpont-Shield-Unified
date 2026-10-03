"""Usage governance API: the workflow menu, resource leases, restrictions, incidents and approvals.

Two audiences:
  /admin/...  admin token. Org-wide usage, forecasts, restrictions, incidents, approvals.
  /me/...     the employee's own API key. Their usage, quota, leases, menu, blocked events, and exactly
              what admins did or looked at about them.

Every restriction an admin sets is written to the admin overlay (validated, hot-reloaded) and logged.
"""

from __future__ import annotations

import calendar
import hmac
import math
import time
from collections.abc import Awaitable, Callable
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..config import Policy, PolicyStore
from ..controls import workflows
from ..controls.access import authenticate
from ..controls.budget import Usage
from ..detections import LEVELS
from ..engine import ControlLayer
from ..types import Context, Direction, Principal

ME_PAGE = Path(__file__).resolve().parent.parent / "dashboard" / "me.html"
DAY = 86400.0
INCIDENT_STATES = {"open", "acknowledged", "resolved", "dismissed"}


def _month_start(now: float) -> float:
    t = time.gmtime(now)
    return calendar.timegm((t.tm_year, t.tm_mon, 1, 0, 0, 0))


def _day_start(now: float) -> float:
    t = time.gmtime(now)
    return calendar.timegm((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0))


def _audit_shape(e: dict[str, Any], direction: str) -> dict[str, Any]:
    """A persisted check event in the audit log's shape (its text is not persisted: `text` is None)."""
    d = e.get("detail") or {}
    findings = []
    for f in d.get("findings") or []:
        head, _, action = str(f).rpartition(":")
        control, _, category = head.partition("/")
        findings.append({"control": control, "category": category, "action": action, "proposed": action,
                         "score": None, "tier": None, "shadow": False, "detail": ""})  # fmt: skip
    return {
        "ts": e["ts"],
        "request_id": e["request_id"],
        "channel": d.get("channel") or e.get("source"),
        "direction": direction,
        "principal": e["principal"],
        "team": e["team"],
        "role": None,
        "model": e.get("model"),
        "tool": e.get("tool"),
        "action": e.get("decision"),
        "status_code": None,
        "reason": d.get("reason") or "",
        "policy_version": None,
        "latency_ms": {},
        "text_sha256": None,
        "text": None,
        "findings": findings,
        "workflow": e.get("workflow"),
        "task": e.get("task"),
        "source": "stored",
    }


def register(
    app: FastAPI,
    store: PolicyStore,
    layer: ControlLayer,
    reclaim: Callable[[dict, str], Awaitable[bool]],
    api_key: Callable[[Request], str | None],
    json_object: Callable[[Request], Awaitable[dict[str, Any]]],
) -> None:
    usage = layer.usage

    def actor(request: Request) -> str:
        return (request.headers.get("x-admin-user") or "admin")[:80]

    def err(msg: str, code: int = 400) -> JSONResponse:
        return JSONResponse({"error": msg}, status_code=code)

    def principals_known(policy: Policy) -> dict[str, Principal]:
        return {k.principal: Principal(k.principal, k.team, k.role) for k in policy.identity.api_keys.values()}

    def menu_view(policy: Policy, who: Principal | None = None) -> list[dict[str, Any]]:
        since = time.time() - 30 * DAY
        rows = []
        paused = None
        if who is not None:
            pp = policy.principal(who.id)
            if pp.status == "revoked":
                paused = f"paused: access revoked ({pp.reason or 'by an administrator'})"
            elif pp.status == "quarantined":
                tools = ", ".join(policy.quarantine.tools)
                paused = f"paused: quarantined, only {tools} tools ({pp.reason or 'by an administrator'})"
        for name, wf in policy.menu.workflows.items():
            why = paused or (workflows.availability(policy, name, wf, who.id, who.team, who.role) if who else None)
            rows.append(
                {
                    "name": name,
                    "description": wf.description,
                    "tier": wf.tier,
                    "enabled": wf.enabled,
                    "approval": wf.approval,
                    "teams": wf.teams,
                    "roles": wf.roles,
                    "models": wf.models,
                    "tools": wf.tools,
                    "resources": {k: v.model_dump() for k, v in wf.resources.items()},
                    "per_run": wf.per_run.model_dump(),
                    "measured": usage.run_stats(name, since),
                    **({"available": why is None, "why": why} if who else {}),
                }
            )
        return rows

    def forecast(now: float, team: str | None = None) -> dict[str, float]:
        start = _month_start(now)
        t = time.gmtime(now)
        days = calendar.monthrange(t.tm_year, t.tm_mon)[1]
        elapsed = max((now - start) / DAY, 1 / 24)
        mtd = usage.spend(start, team=team)
        return {
            "today": round(usage.spend(_day_start(now), team=team), 6),
            "month_to_date": round(mtd, 6),
            "month_forecast": round(mtd / elapsed * days, 6),
        }

    def principal_row(policy: Policy, p: Principal) -> dict[str, Any]:
        pp = policy.principal(p.id)
        day = time.strftime("%Y-%m-%d", time.gmtime())
        u = layer.ledger.usage.get(("principal", p.id, day), Usage())
        score = layer.risk.score(p.id, policy)
        return {
            "principal": p.id,
            "team": p.team,
            "role": p.role,
            "status": pp.status,
            "budget_scale": policy.budget_scale(p.id),
            "approved_workflows": pp.approved_workflows,
            "reason": pp.reason,
            "by": pp.by,
            "since": pp.since,
            "risk": score,
            "level": layer.risk.level(score, policy),
            "open_incidents": sum(
                1 for i in layer.risk.incidents if i["principal"] == p.id and i["status"] in ("open", "acknowledged")
            ),
            "today": {"requests": u.requests, "tokens": u.tokens, "usd": round(u.usd, 4)},
            "leases": len(layer.leases.held(p.id)),
        }

    def events_of(pid: str, limit: int = 100, interesting: bool = False) -> list[dict]:
        """One person's policy checks, newest first: the audit ring (with masked text) merged with the
        persisted event stream, which survives restarts and holds seeded history (no text there)."""
        ring = [
            e
            for e in reversed(layer.audit.events)
            if e["principal"] == pid and (not interesting or e["action"] != "allow")
        ]
        seen = {(e["request_id"], e["direction"]) for e in ring}
        stored = []
        kinds = [f"check.{d.value}" for d in Direction]
        for e in usage.events(principal=pid, kind=kinds, limit=limit + len(ring)):
            direction = e["kind"].removeprefix("check.")
            if (e["request_id"], direction) in seen or (interesting and e["decision"] == "allow"):
                continue
            stored.append(_audit_shape(e, direction))
        out = sorted([*ring, *stored], key=lambda e: e["ts"], reverse=True)
        return out[:limit]

    # ---- admin: usage and forecasts ---------------------------------------------

    @app.get("/admin/overview")
    async def overview():
        p, now = store.policy, time.time()
        leases = layer.leases.snapshot(p)
        teams = sorted({k.team for k in p.identity.api_keys.values()})
        guard = usage.breakdown(["resource"], _day_start(now))
        return {
            "spend": forecast(now),
            "teams": {t: forecast(now, t) for t in teams},
            "global_usd_per_day": p.budgets.global_.usd_per_day,
            "guard_tokens_today": sum(r["guard_tokens"] or 0 for r in guard),
            "leases_open": len(leases),
            "leases_running_usd": round(sum(x["running_usd"] for x in leases), 4),
            "zombies": sum(1 for x in leases if x["flags"]),
            "incidents_open": sum(1 for i in layer.risk.incidents if i["status"] == "open"),
            "requests_pending": len(usage.requests(status="pending")),
            "at_risk": sum(1 for s in layer.risk.scores(p).values() if s >= p.detections.response.alert),
        }

    @app.get("/admin/usage")
    async def usage_breakdown(by: str = "workflow", days: float = 1, principal: str | None = None):
        try:
            since = _day_start(time.time()) if days == 1 else time.time() - days * DAY
            return usage.breakdown(by.split(","), since, principal)
        except ValueError as e:
            return err(str(e))

    # ---- admin: the menu ----------------------------------------------------------

    @app.get("/admin/menu")
    async def admin_menu():
        p = store.policy
        return {
            "require_label": p.menu.require_label,
            "classify_unlabeled": p.menu.classify_unlabeled,
            "workflows": menu_view(p),
        }

    @app.post("/admin/menu/{name}")
    async def admin_menu_edit(name: str, request: Request):
        body = await json_object(request)
        if name not in store.policy.menu.workflows:
            return err(f"unknown workflow {name!r}", 404)
        allowed = {"enabled", "approval", "per_run", "teams", "roles", "models", "tools", "tier", "resources"}
        patch = {k: v for k, v in body.items() if k in allowed}
        if not patch:
            return err(f"nothing to change; fields: {sorted(allowed)}")
        return _write(request, {"menu": {"workflows": {name: patch}}}, "edit_workflow", name, body.get("reason", ""))

    # ---- admin: people and restrictions -------------------------------------------

    @app.get("/admin/principals")
    async def admin_principals():
        p = store.policy
        known = principals_known(p)
        for row in usage.breakdown(["principal", "team"], time.time() - 30 * DAY):
            known.setdefault(row["principal"], Principal(row["principal"], row["team"], "?"))
        rows = [principal_row(p, x) for x in known.values()]
        return sorted(rows, key=lambda r: (-r["risk"], r["principal"]))

    @app.post("/admin/principals/{pid}")
    async def admin_principal_edit(pid: str, request: Request):
        """Quarantine, revoke, restore, rescale a budget or approve workflows for one person."""
        body = await json_object(request)
        reason = str(body.get("reason") or "").strip()
        if not reason:
            return err("a reason is required; it is shown to the employee")
        if body.get("clear"):
            # Restrictions go; what was approved for them (grants, workflows) stays: revoking is its own action.
            reset = dict.fromkeys(("status", "budget_scale", "reason", "by", "since"))
            resp = _write(request, {"principals": {pid: reset}}, "clear_restrictions", pid, reason)
        else:
            patch: dict[str, Any] = {k: body[k] for k in ("status", "budget_scale", "approved_workflows") if k in body}
            if not patch:
                return err("set status, budget_scale, approved_workflows or clear")
            patch |= {"reason": reason, "by": actor(request), "since": time.time()}
            resp = _write(request, {"principals": {pid: patch}}, "restrict", pid, reason)
        lifted = body.get("clear") or (body.get("status") == "active" and body.get("budget_scale", 1) >= 1)
        if resp.status_code == 200 and lifted:
            # Lifting a restriction settles the incidents behind it; otherwise the next event re-applies it.
            for i in layer.risk.incidents:
                if i["principal"] == pid and i["status"] in ("open", "acknowledged"):
                    layer.risk.set_status(i["id"], "resolved", f"restrictions lifted: {reason}")
        return resp

    @app.get("/admin/principals/{pid}/events")
    async def admin_principal_events(pid: str, request: Request, reason: str = "", limit: int = 100):
        """One person's events (masked). Needs a reason, which is logged and shown to that person."""
        if store.policy.privacy.require_reason_for_content and not reason.strip():
            return err("state a reason to view one person's events (?reason=...); it is logged and shown to them")
        usage.log_admin(actor(request), "view_events", pid, reason.strip())
        return events_of(pid, limit)

    def _write(request: Request, patch: dict, action: str, target: str, reason: str) -> JSONResponse:
        try:
            p = store.write_overlay(patch)
        except ValueError as e:
            return err(f"rejected: {e}", 422)
        usage.log_admin(actor(request), action, target, reason, patch)
        return JSONResponse({"ok": True, "version": p.version})

    @app.get("/admin/actions")
    async def admin_actions(target: str | None = None):
        return usage.admin_actions(target)

    # ---- admin: leases ---------------------------------------------------------------

    @app.get("/admin/leases")
    async def admin_leases():
        p = store.policy
        closed = [x for x in usage.leases(limit=50) if x["ended"]]
        return {"open": layer.leases.snapshot(p), "recent": closed}

    @app.post("/admin/leases/{lease_id}/release")
    async def admin_release(lease_id: str, request: Request):
        return await _release(lease_id, None, f"released by {actor(request)}")

    async def _release(lease_id: str, owner: str | None, reason: str) -> JSONResponse:
        lease = layer.leases.open.get(lease_id)
        if lease is None or (owner and lease["principal"] != owner):
            return err("no such open lease", 404)
        if await reclaim(lease, reason):
            return JSONResponse({"ok": True, "stopped": True})
        # No way to stop it from here: close the lease so it stops billing, and say so.
        layer.leases.close(lease_id, store.policy, reason + " (resource not stopped by the gateway)")
        return JSONResponse({"ok": True, "stopped": False})

    # ---- admin: incidents and approvals ---------------------------------------------

    @app.get("/admin/incidents")
    async def admin_incidents(status: str | None = None, limit: int = 200):
        p = store.policy
        rows = [i for i in reversed(layer.risk.incidents) if not status or i["status"] == status][:limit]
        return {"incidents": rows, "scores": layer.risk.scores(p), "levels": LEVELS}

    @app.post("/admin/incidents/{iid}")
    async def admin_incident_edit(iid: str, request: Request):
        body = await json_object(request)
        status = body.get("status")
        if status not in INCIDENT_STATES:
            return err(f"status must be one of {sorted(INCIDENT_STATES)}")
        if not layer.risk.set_status(iid, status, str(body.get("note", ""))):
            return err("no such incident", 404)
        usage.log_admin(actor(request), f"incident_{status}", iid, str(body.get("note", "")))
        return {"ok": True}

    @app.get("/admin/requests")
    async def admin_requests(status: str | None = None):
        return usage.requests(status=status)

    @app.post("/admin/requests/{rid}")
    async def admin_request_decide(rid: str, request: Request):
        body = await json_object(request)
        decision = body.get("decision")
        if decision not in ("approve", "deny"):
            return err("decision must be approve or deny")
        pending = [r for r in usage.requests(status="pending") if r["id"] == rid]
        if not pending:
            return err("no such pending request", 404)
        req, note = pending[0], str(body.get("note", ""))
        if decision == "approve":
            pp = store.policy.principal(req["principal"])
            if req["kind"] == "grant":
                grants = app.state.grant_for_request(
                    store.policy,
                    req["principal"],
                    req["detail"] or {},
                    actor(request),
                    f"request {rid}: {req['reason']}",
                )
                if isinstance(grants, str):
                    return err(grants, 422)
                patch = {"grants": grants}
            elif req["kind"] == "workflow":
                patch = {"approved_workflows": sorted({*pp.approved_workflows, req["workflow"]})}
            else:
                # Never lowers the person's scale; a quarantine still caps what applies (Policy.budget_scale).
                patch = {"budget_scale": max(pp.budget_scale, float(req["scale"] or 2.0))}
            if pp.status == "active":
                patch |= {"reason": f"request {rid} approved: {req['reason']}", "by": actor(request)}
                patch["since"] = time.time()
            # Quarantined or revoked: the restriction's reason, author and start stay as they were.
            resp = _write(request, {"principals": {req["principal"]: patch}}, "approve_request", req["principal"], note)
            if resp.status_code != 200:
                return resp
        usage.decide_request(rid, "approved" if decision == "approve" else "denied", actor(request), note)
        p = store.policy
        return {"ok": True, "status": p.principal(req["principal"]).status,
                "budget_scale": p.budget_scale(req["principal"])}  # fmt: skip

    # ---- admin: charts, adherence, evidence, people, activity ---------------------------

    def pivot(rows: list[dict[str, Any]], days: int, now: float) -> dict[str, Any]:
        """Rows of (day, key, value) as aligned daily series, zero-filled, largest series first."""
        start = _day_start(now) - (days - 1) * DAY
        labels = [time.strftime("%Y-%m-%d", time.gmtime(start + i * DAY)) for i in range(days)]
        idx = {d: i for i, d in enumerate(labels)}
        series: dict[str, list[float]] = {}
        for r in rows:
            if r["day"] in idx:
                series.setdefault(r["key"], [0.0] * days)[idx[r["day"]]] += round(r["value"] or 0, 6)
        ordered = dict(sorted(series.items(), key=lambda kv: -sum(kv[1])))
        return {
            "days": labels,
            "series": ordered,
            "totals": [round(sum(v[i] for v in ordered.values()), 6) for i in range(days)],
        }

    def hidden_from(principal: str | None) -> tuple[str, ...]:
        """Event sources an employee does not see about themselves: incidents, unless the policy shows risk."""
        return () if principal is None or store.policy.privacy.show_risk_to_employee else ("detections",)

    def timeseries(metric: str, by: str | None, days: int, principal: str | None, own: bool = False):
        days = max(1, min(int(days), 120))
        now = time.time()
        try:
            rows = usage.timeseries(
                metric,
                by or None,
                _day_start(now) - (days - 1) * DAY,
                principal,
                exclude_sources=hidden_from(principal) if own else (),
            )
        except ValueError as e:
            return err(str(e))
        return {"metric": metric, "by": by, **pivot(rows, days, now)}

    @app.get("/admin/timeseries")
    async def admin_timeseries(metric: str = "usd", by: str | None = "workflow", days: int = 30):
        return timeseries(metric, by, days, None)

    @app.get("/admin/adherence")
    async def admin_adherence(by: str | None = "team", days: int = 30):
        """Share of policy checks that needed no intervention (allow or log), per team, workflow, source..."""
        since = time.time() - max(1, min(days, 400)) * DAY
        try:
            rows = usage.adherence(by or None, since)
            overall = usage.adherence(None, since)
        except ValueError as e:
            return err(str(e))
        return {"by": by, "rows": rows, "overall": overall[0] if overall else None}

    def incident_by_id(iid: str) -> dict[str, Any] | None:
        hit = next((i for i in layer.risk.incidents if i["id"] == iid), None)
        return hit or next((i for i in usage.incidents(since=0, limit=10_000) if i["id"] == iid), None)

    @app.get("/admin/incidents/{iid}")
    async def admin_incident(iid: str):
        """One incident with its evidence: the events it names, and what the person did around it."""
        inc = incident_by_id(iid)
        if inc is None:
            return err("no such incident", 404)
        ids = [e for e in inc.get("evidence") or [] if e]
        named = usage.events(request_id=ids, limit=200) if ids else []
        around = usage.events(principal=inc["principal"], since=inc["ts"] - 3600, until=inc["ts"] + 900, limit=200)
        seen, timeline = set(), []
        for e in sorted([*named, *around], key=lambda e: e["ts"]):
            if e["id"] in seen:
                continue
            seen.add(e["id"])
            timeline.append({**e, "evidence": e.get("request_id") in ids or e["id"] in ids})
        p = store.policy
        score = layer.risk.score(inc["principal"], p)
        actions = [a for a in usage.admin_actions(inc["principal"]) if a["ts"] >= inc["ts"] - 60]
        return {
            "incident": inc,
            "timeline": timeline,
            "principal": {
                "id": inc["principal"],
                "risk": score,
                "level": layer.risk.level(score, p),
                "status": p.principal(inc["principal"]).status,
            },  # fmt: skip
            "actions": actions,
        }

    @app.get("/admin/people/{pid}")
    async def admin_person(pid: str, days: int = 30):
        """Everything about one person except the content of their events (that needs a stated reason)."""
        p, now = store.policy, time.time()
        known = principals_known(p)
        if pid not in known:
            rows = usage.breakdown(["principal", "team"], now - 90 * DAY, pid)
            if not rows:
                return err("no such person", 404)
            known[pid] = Principal(pid, rows[0]["team"], "?")
        since = now - max(1, min(days, 120)) * DAY
        return {
            **principal_row(p, known[pid]),
            "email": ident.email if (ident := p.identity_of(pid)) else None,
            "spend": timeseries("usd", "workflow", days, pid),
            "by_workflow": usage.breakdown(["workflow"], since, pid),
            "by_resource": usage.breakdown(["resource"], since, pid),
            "by_source": usage.breakdown(["source"], since, pid),
            "adherence": (usage.adherence(None, since, pid) or [None])[0],
            "incidents": [i for i in reversed(layer.risk.incidents) if i["principal"] == pid][:50],
            "grants": [{**g.model_dump(), "live": g.live(now)} for g in p.principal(pid).grants],
            "leases": layer.leases.snapshot(p, pid),
            "requests": usage.requests(principal=pid),
            "admin_actions": usage.admin_actions(pid),
        }

    @app.get("/admin/activity")
    async def admin_activity(
        limit: int = 50,
        before: float | None = None,
        source: str | None = None,
        kind: str | None = None,
        principal: str | None = None,
        severity: str | None = None,
    ):
        """The live feed: newest events first (no content; masked text stays in the audit log)."""
        return usage.events(limit=max(1, min(limit, 500)), before=before, source=source, kind=kind,
                            principal=principal, severity=severity)  # fmt: skip

    # ---- employees ---------------------------------------------------------------------

    def me(request: Request) -> Principal | None:
        key = api_key(request) or request.query_params.get("key")
        p = authenticate(store.policy, key)
        return p if p.authenticated else None

    @app.get("/me", response_class=HTMLResponse)
    async def me_page():
        return ME_PAGE.read_text() if ME_PAGE.exists() else "<p>employee dashboard not built</p>"

    @app.get("/me/summary")
    async def me_summary(request: Request):
        who = me(request)
        if who is None:
            return err("your API key is required", 401)
        p, now = store.policy, time.time()
        pp = p.principal(who.id)
        snap = layer.ledger.snapshot(p)
        mine = [r for r in snap["scopes"] if (r["scope"], r["key"]) in (("principal", who.id), ("team", who.team))]
        today, week = _day_start(now), now - 7 * DAY
        score = layer.risk.score(who.id, p)
        leases = layer.leases.snapshot(p, who.id)
        by_workflow = usage.breakdown(["workflow"], week, who.id)
        out: dict[str, Any] = {
            "principal": who.id,
            "team": who.team,
            "role": who.role,
            "status": {
                "status": pp.status,
                "budget_scale": p.budget_scale(who.id),
                "reason": pp.reason,
                "by": pp.by,
                "since": pp.since,
                "approved_workflows": pp.approved_workflows,
            },
            "grants": [{**g.model_dump(), "live": g.live(now)} for g in pp.grants if g.live(now - DAY)],
            "budgets": mine,
            "spend": {
                "today": round(sum(r["usd"] for r in usage.breakdown(["principal"], today, who.id)), 6),
                "month_to_date": round(
                    sum(r["usd"] for r in usage.breakdown(["principal"], _month_start(now), who.id)), 6
                ),
            },
            "by_workflow": by_workflow,
            "by_resource": usage.breakdown(["resource"], week, who.id),
            "by_model": usage.breakdown(["model"], week, who.id),
            "runs": usage.breakdown(["workflow", "task"], week, who.id)[:30],
            "leases": leases,
            "menu": menu_view(p, who),
            "events": events_of(who.id, 50, interesting=True),
            "requests": usage.requests(principal=who.id),
            "admin_activity": usage.admin_actions(who.id),
            "tips": tips(p, who, leases, by_workflow, now),
            "collected": [
                "who, team, role; model and tool used; workflow and task labels",
                "tokens, cost, resource minutes per call",
                "prompt text with detected PII and secrets masked (blocked prompts are not stored)",
                "client IP and user agent, to detect a stolen key",
            ],
        }
        if p.privacy.show_risk_to_employee:
            out["risk"] = {
                "score": score,
                "level": layer.risk.level(score, p),
                "incidents": [i for i in reversed(layer.risk.incidents) if i["principal"] == who.id][:20],
            }
        return out

    def tips(p: Policy, who: Principal, leases: list[dict], by_workflow: list[dict], now: float) -> list[str]:
        out = []
        for x in leases:
            if x["idle_minutes"] >= 5:
                rate = p.resources[x["resource"]].usd_per_minute * 60 if x["resource"] in p.resources else 0
                out.append(
                    f"{x['resource']} {x['handle'] or x['id']} has been idle {x['idle_minutes']:.0f} min"
                    f" (${rate:.2f}/h). Stop it if you are done."
                )
        total = sum(r["tokens"] or 0 for r in by_workflow)
        unl = sum(r["tokens"] or 0 for r in by_workflow if r["workflow"] in ("(none)", workflows.UNLABELED))
        if total and unl / total > 0.3:
            out.append(
                f"{unl / total:.0%} of your tokens this week have no workflow label. Send x-acl-workflow and"
                " x-acl-task headers to see what each task costs."
            )
        models = usage.breakdown(["model"], now - 7 * DAY, who.id)
        spend = sum(r["usd"] or 0 for r in models)
        if spend:
            top = max(models, key=lambda r: r["usd"] or 0)
            route = next(
                (to for pat, to in p.budgets.downgrade.routes.items() if fnmatch(top["model"] or "", pat)),
                None,
            )
            if route and (top["usd"] or 0) / spend > 0.5:
                out.append(
                    f"{top['usd'] / spend:.0%} of your spend is {top['model']}. For light tasks, {route} is cheaper."
                )
        pp = p.principal(who.id)
        if p.budget_scale(who.id) < 1 and pp.status == "active":
            out.append(f"Your daily budget is at {p.budget_scale(who.id):.0%} of normal: {pp.reason}")
        return out

    @app.get("/me/events")
    async def me_events(request: Request, limit: int = 100):
        who = me(request)
        if who is None:
            return err("your API key is required", 401)
        return events_of(who.id, limit)

    @app.post("/me/leases/{lease_id}/release")
    async def me_release(lease_id: str, request: Request):
        who = me(request)
        if who is None:
            return err("your API key is required", 401)
        return await _release(lease_id, who.id, f"released by {who.id}")

    @app.get("/me/timeseries")
    async def me_timeseries(request: Request, metric: str = "usd", by: str | None = "workflow", days: int = 30):
        who = me(request)
        if who is None:
            return err("your API key is required", 401)
        return timeseries(metric, by, days, who.id, own=True)

    @app.get("/me/activity")
    async def me_activity(request: Request, limit: int = 50, before: float | None = None):
        who = me(request)
        if who is None:
            return err("your API key is required", 401)
        return usage.events(
            principal=who.id, limit=max(1, min(limit, 500)), before=before, exclude_sources=hidden_from(who.id)
        )

    @app.post("/me/requests")
    async def me_request(request: Request):
        """Ask for a workflow that needs approval, more daily budget, or a time-boxed grant to a resource."""
        who = me(request)
        if who is None:
            return err("your API key is required", 401)
        body = await json_object(request)
        kind, reason = body.get("kind"), str(body.get("reason") or "").strip()
        if kind not in ("workflow", "quota", "grant") or not reason:
            return err("kind must be workflow, quota or grant, with a reason")
        detail = None
        if kind == "grant":
            r = store.policy.catalog.get(str(body.get("resource") or ""))
            if r is None or r.class_ != "access_grant":
                return err(f"{body.get('resource')!r} is not a resource that can be granted")
            try:
                minutes = float(body.get("minutes") or (r.grant.max_minutes if r.grant else 60))
            except (TypeError, ValueError):
                return err("minutes must be a number")
            if not math.isfinite(minutes) or minutes <= 0:
                return err("minutes must be a positive number")
            detail = {"resource": body["resource"], "minutes": minutes}
            if isinstance(body.get("actions"), list):
                detail["actions"] = [a for a in body["actions"] if isinstance(a, str)]
            if body.get("workflow") in store.policy.menu.workflows:
                detail["workflow"] = body["workflow"]
        wf = body.get("workflow")
        if kind == "workflow" and wf not in store.policy.menu.workflows:
            return err(f"unknown workflow {wf!r}")
        scale = None
        if kind == "quota":
            try:
                scale = float(body.get("scale", 2.0))
            except (TypeError, ValueError):
                return err("scale must be a number")
            if not 0 < scale <= 10:
                return err("scale must be between 0 and 10")
        rid = usage.add_request(who.id, kind, wf if kind == "workflow" else None, scale, reason[:500], detail)
        return {"ok": True, "id": rid}

    # ---- external meters -------------------------------------------------------------------

    @app.post("/v1/usage")
    async def report_usage(request: Request):
        """Usage the gateway cannot see (CI minutes, cloud VMs, GPU jobs), reported by the runner.

        Authenticated with the employee's key, or with the admin token plus a `principal` field."""
        body = await json_object(request)
        p = store.policy
        who = authenticate(p, api_key(request))
        token = p.identity.admin_token
        given = request.headers.get("x-admin-token") or ""
        if not who.authenticated and body.get("principal") and token and hmac.compare_digest(given, token):
            who = principals_known(p).get(str(body["principal"]), who)
        if not who.authenticated:
            return err("API key (or admin token and principal) required", 401)
        if p.principal(who.id).status == "revoked":
            return err("access revoked", 401)
        resource = body.get("resource")
        r = p.resources.get(resource) if isinstance(resource, str) else None
        if r is None:
            return err(f"unknown resource {resource!r}; configured: {sorted(p.resources)}")
        unit = body.get("unit", "minute")
        try:
            quantity = float(body.get("quantity"))
        except (TypeError, ValueError):
            return err("quantity must be a number")
        if unit not in ("minute", "unit") or not 0 < quantity < 1e7:
            return err("unit must be minute or unit, with a positive quantity")
        usd = quantity * (r.usd_per_minute if unit == "minute" else r.usd_per_unit)
        task = body.get("task_id") or request.headers.get("x-acl-task")
        wf = body.get("workflow") or request.headers.get("x-acl-workflow")
        if not wf and isinstance(task, str):
            wf = usage.workflow_of_task(who.id, task)
        ctx = Context(
            who,
            Direction.TOOL_CALL,
            "",
            channel="usage",
            workflow=wf,
            workflow_source="declared" if wf else None,
            task_id=task,
        )
        layer.ledger.charge(ctx, resource, usd, quantity, unit, ref=ctx.request_id, source="report")
        usage.add_event(
            {
                "source": "report",
                "kind": "usage.report",
                "principal": who.id,
                "team": who.team,
                "task": task,
                "workflow": wf,
                "resource": resource,
                "urn": p.catalog[resource].urn if resource in p.catalog else None,
                "usd": usd,
                "severity": "info",
                "request_id": ctx.request_id,
                "detail": {"quantity": quantity, "unit": unit},
            }
        )
        return {"ok": True, "resource": resource, "quantity": quantity, "unit": unit, "usd": round(usd, 6)}
