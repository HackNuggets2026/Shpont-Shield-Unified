"""Organization views for a company of thousands: departments, teams, people search and outliers.

  GET /admin/org                 the company and its departments over a window, with the previous window
  GET /admin/org/teams           teams, sortable and paged
  GET /admin/org/unit            one department or team: metrics, spend, adherence, workflows, outliers
  GET /admin/people              search the directory (paged); replaces any full list
  GET /admin/outliers            people far above their team, the riskiest, the fastest growing

Individual people appear only as outliers, in search, or inside one unit.
"""

from __future__ import annotations

import os
import statistics
import threading
import time
from collections import defaultdict
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ..config import Policy, PolicyStore
from ..engine import ControlLayer
from ..usage import day_of

DAY = 86400.0
TEAM_SORTS = ("usd", "usd_prev", "usd_per_active", "adherence", "adherence_prev", "risk", "people_at_risk",
              "incidents_open", "headcount", "active", "tokens", "checks", "interventions", "claude_code_users",
              "claude_code_usd", "name")  # fmt: skip
PEOPLE_SORTS = ("risk", "usd", "tokens", "name")
STATUSES = ("active", "quarantined", "revoked", "limited", "watch", "restricted")  # the last two: insider-risk level


# Aggregate GETs answered from the cache (the whole response body, per query string).
CACHED_PATHS = frozenset({
    "/admin/org", "/admin/org/teams", "/admin/org/unit", "/admin/people", "/admin/outliers",
    "/admin/incidents/summary", "/admin/timeseries", "/admin/adherence", "/admin/usage", "/admin/value",
    "/admin/overview", "/admin/catalog", "/admin/menu", "/admin/principals",
})  # fmt: skip


class AdminCache:
    """Aggregate GETs for `ACL_ADMIN_CACHE_SECONDS` (15 s). Dropped whenever an admin POST changes something,
    the policy reloads (an automatic response) or a detection opens an incident."""

    def __init__(self, ttl: float | None = None):
        self._ttl = ttl
        self.data: dict[Any, tuple[float, Any]] = {}
        self.lock = threading.Lock()

    @property
    def ttl(self) -> float:
        if self._ttl is not None:
            return self._ttl
        try:
            return float(os.environ.get("ACL_ADMIN_CACHE_SECONDS", "15"))
        except ValueError:
            return 15.0

    def __call__(self, key: Any, fn) -> Any:
        ttl = self.ttl
        if ttl <= 0:
            return fn()
        now = time.monotonic()
        hit = self.data.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        value = fn()
        with self.lock:
            if len(self.data) > 500:
                self.data.clear()
            self.data[key] = (now, value)
        return value

    def peek(self, key: Any) -> Any | None:
        hit = self.data.get(key)
        return hit[1] if hit and time.monotonic() - hit[0] < self.ttl else None

    def put(self, key: Any, value: Any) -> None:
        with self.lock:
            if len(self.data) > 500:
                self.data.clear()
            self.data[key] = (time.monotonic(), value)

    def clear(self, *_: Any) -> None:
        with self.lock:
            self.data.clear()


def day_start(now: float) -> float:
    return (now // DAY) * DAY


def window(days: int, now: float) -> tuple[int, float, float]:
    """(days, since, previous window's start): `days` whole UTC days, today included."""
    days = max(1, min(int(days), 120))
    since = day_start(now) - (days - 1) * DAY
    return days, since, since - days * DAY


class OrgView:
    """Per-person window totals joined to the directory, rolled up to teams and departments."""

    def __init__(self, store: PolicyStore, layer: ControlLayer):
        self.store = store
        self.layer = layer
        self.usage = layer.usage

    # ---- per person ----------------------------------------------------------------------

    def person_totals(self, since: float, prev: float) -> dict[str, dict[str, Any]]:
        d, p = day_of(since), day_of(prev)
        rows = self.usage._q(
            "SELECT principal, MAX(team) team,"
            " SUM(CASE WHEN day>=? THEN usd ELSE 0 END) usd,"
            " SUM(CASE WHEN day<? THEN usd ELSE 0 END) usd_prev,"
            " SUM(CASE WHEN day>=? THEN tokens ELSE 0 END) tokens,"
            " SUM(CASE WHEN day>=? THEN cc_usd ELSE 0 END) cc_usd"
            " FROM person_day WHERE day>=? GROUP BY principal",
            (d, d, d, d, p),
        )
        return {r["principal"]: r for r in rows}

    def unit_of(self, pid: str, team: str | None) -> tuple[str, str]:
        """(department, team) from the directory, else from the usage row's team."""
        p = self.usage.person(pid)
        t = (p or {}).get("team") or team or "unattributed"
        return self.usage.department_of(pid, t), t

    def people(self, since: float, prev: float) -> list[dict[str, Any]]:
        """Everyone in the directory, plus anyone with usage in the window who is not in it."""
        totals = self.person_totals(since, prev)
        out = []
        seen = set()
        for pid, p in self.usage.people.items():
            t = totals.get(pid) or {}
            out.append(self._row(pid, p, t))
            seen.add(pid)
        for pid, t in totals.items():
            if pid not in seen:
                out.append(self._row(pid, None, t))
        return out

    def _row(self, pid: str, p: dict | None, t: dict) -> dict[str, Any]:
        dept, team = self.unit_of(pid, t.get("team"))
        return {
            "principal": pid,
            "name": (p or {}).get("name") or pid,
            "email": (p or {}).get("email"),
            "team": team,
            "department": dept,
            "role": (p or {}).get("role"),
            "title": (p or {}).get("title"),
            "in_directory": p is not None,
            "usd": round(t.get("usd") or 0.0, 4),
            "usd_prev": round(t.get("usd_prev") or 0.0, 4),
            "tokens": int(t.get("tokens") or 0),
            "cc_usd": round(t.get("cc_usd") or 0.0, 4),
        }

    def risk(self, policy: Policy) -> tuple[dict[str, float], dict[str, int], set[str]]:
        """Scores, open incidents per person (all time), and the people at risk: a risk score at or above the
        watch threshold (the same definition as the overview's `at_risk`)."""
        scores = self.layer.detections.scores(policy)
        open_: dict[str, int] = defaultdict(int)
        for r in self.usage._q("SELECT principal, COUNT(*) n FROM incidents WHERE status='open' GROUP BY principal"):
            open_[r["principal"]] = r["n"]
        watch = policy.insider_risk.levels.watch
        at_risk = {p for p, s in scores.items() if s >= watch}
        return scores, open_, at_risk

    # ---- per unit ---------------------------------------------------------------------------

    def unit_events(self, since: float, prev: float) -> dict[tuple[str, str], dict[str, int]]:
        """Checks and interventions per (department, team), in the window and in the one before it."""
        out: dict[tuple[str, str], dict[str, int]] = defaultdict(
            lambda: {"checks": 0, "interventions": 0, "checks_prev": 0, "interventions_prev": 0}
        )
        d = day_of(since)
        for r in self.usage._q(
            "SELECT department d, team t, day>=? cur, decision IN ('warn', 'redact', 'block') iv, SUM(n) n"
            " FROM event_day WHERE day>=? AND kind LIKE 'check.%' GROUP BY d, t, cur, iv",
            (d, day_of(prev)),
        ):
            o = out[(r["d"], _team(r["t"]))]
            suffix = "" if r["cur"] else "_prev"
            o["checks" + suffix] += r["n"]
            if r["iv"]:
                o["interventions" + suffix] += r["n"]
        return out

    def unit_workflows(self, since: float) -> dict[tuple[str, str], dict[str, float]]:
        out: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
        for r in self.usage._q(
            "SELECT department d, team t, workflow w, SUM(usd) usd FROM unit_day WHERE day>=? GROUP BY d, t, w",
            (day_of(since),),
        ):
            out[(r["d"], _team(r["t"]))][r["w"]] = r["usd"] or 0.0
        return out

    def snapshot(self, days: int, now: float | None = None) -> dict[str, Any]:
        """Everything the org views need for one window, computed once (the caller caches it)."""
        now = now or time.time()
        days, since, prev = window(days, now)
        policy = self.store.policy
        people = self.people(since, prev)
        scores, open_, at_risk = self.risk(policy)
        events = self.unit_events(since, prev)
        wfs = self.unit_workflows(since)
        for r in people:
            r["risk"] = scores.get(r["principal"], 0.0)
            r["open_incidents"] = open_.get(r["principal"], 0)
            r["at_risk"] = r["principal"] in at_risk
        return {"days": days, "since": since, "prev": prev, "now": now, "people": people, "events": events,
                "workflows": wfs}  # fmt: skip

    @staticmethod
    def metrics(members: list[dict], events: list[dict[str, int]], wf: dict[str, float]) -> dict[str, Any]:
        active = [m for m in members if m["usd"] > 0 or m["tokens"] > 0]
        usd = sum(m["usd"] for m in members)
        checks = sum(e["checks"] for e in events)
        interventions = sum(e["interventions"] for e in events)
        checks_prev = sum(e["checks_prev"] for e in events)
        interventions_prev = sum(e["interventions_prev"] for e in events)
        top = max(wf.items(), key=lambda kv: kv[1])[0] if wf else None
        return {
            "headcount": sum(1 for m in members if m["in_directory"]),
            "active": len(active),
            "usd": round(usd, 2),
            "usd_prev": round(sum(m["usd_prev"] for m in members), 2),
            "usd_per_active": round(usd / len(active), 2) if active else 0.0,
            "tokens": sum(m["tokens"] for m in members),
            "checks": checks,
            "interventions": interventions,
            "adherence": round((checks - interventions) / checks, 4) if checks else None,
            "adherence_prev": round((checks_prev - interventions_prev) / checks_prev, 4) if checks_prev else None,
            "incidents_open": sum(m["open_incidents"] for m in members),
            "people_at_risk": sum(1 for m in members if m["at_risk"]),
            "claude_code_users": sum(1 for m in members if m["cc_usd"] > 0),
            "claude_code_usd": round(sum(m["cc_usd"] for m in members), 2),
            "top_workflow": top,
        }

    def departments(self, snap: dict) -> list[dict[str, Any]]:
        policy = self.store.policy
        by_dept: dict[str, list[dict]] = defaultdict(list)
        for m in snap["people"]:
            by_dept[m["department"]].append(m)
        names = list(policy.org.departments) + sorted(d for d in by_dept if d not in policy.org.departments)
        rows = []
        for d in names:
            members = by_dept.get(d, [])
            teams = self.team_names(d, members)
            ev = [v for (dd, _), v in snap["events"].items() if dd == d]
            wf: dict[str, float] = defaultdict(float)
            for (dd, _), w in snap["workflows"].items():
                if dd == d:
                    for k, v in w.items():
                        wf[k] += v
            owner = policy.org.departments[d].owner if d in policy.org.departments else ""
            rows.append({"name": d, "owner": owner, "teams": len(teams), **self.metrics(members, ev, wf)})
        return rows

    def team_names(self, dept: str, members: list[dict]) -> list[str]:
        policy = self.store.policy
        listed = policy.org.departments[dept].teams if dept in policy.org.departments else []
        return list(dict.fromkeys([*listed, *sorted({m["team"] for m in members})]))

    def teams(self, snap: dict, department: str | None = None) -> list[dict[str, Any]]:
        policy = self.store.policy
        by_team: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for m in snap["people"]:
            by_team[(m["department"], m["team"])].append(m)
        keys = {k for k in by_team} | {(d, t) for d, x in policy.org.departments.items() for t in x.teams}
        rows = []
        for d, t in sorted(keys):
            if department and d != department:
                continue
            members = by_team.get((d, t), [])
            ev = [snap["events"][(d, t)]] if (d, t) in snap["events"] else []
            manager = next((m["principal"] for m in members if m["role"] == "manager"), None)
            owner = manager or (policy.org.departments[d].owner if d in policy.org.departments else "")
            rows.append({"name": t, "department": d, "owner": owner, "teams": 1,
                         **self.metrics(members, ev, snap["workflows"].get((d, t), {}))})  # fmt: skip
        return rows

    # ---- outliers ---------------------------------------------------------------------------

    def outliers(
        self, snap: dict, limit: int, department: str | None = None, team: str | None = None
    ) -> dict[str, list]:
        people = [m for m in snap["people"] if (not department or m["department"] == department)
                  and (not team or m["team"] == team)]  # fmt: skip
        spend: dict[str, list[float]] = defaultdict(list)
        for m in snap["people"]:  # the team median is over the whole team, whatever the filter
            if m["usd"] > 0:
                spend[m["team"]].append(m["usd"])
        medians = {t: statistics.median(v) for t, v in spend.items() if len(v) >= 3}
        cost = []
        for m in people:
            med = medians.get(m["team"])
            if med and m["usd"] >= 3 * med:
                cost.append({**_who(m), "usd": round(m["usd"], 2), "team_median": round(med, 2),
                             "ratio": round(m["usd"] / med, 2)})  # fmt: skip
        cost.sort(key=lambda r: -r["ratio"])
        policy = self.store.policy
        risk = []
        for m in people:
            if m["risk"] > 0 or m["open_incidents"]:
                risk.append({**_who(m), "risk": m["risk"], "level": self.layer.detections.level(m["risk"], policy, m["principal"]),
                             "status": policy.principal(m["principal"]).status,
                             "open_incidents": m["open_incidents"]})  # fmt: skip
        risk.sort(key=lambda r: (-r["risk"], -r["open_incidents"], r["principal"]))
        growth = []
        floor = 5.0 * snap["days"] / 30  # ignore growth from a near-zero base
        for m in people:
            if m["usd_prev"] >= floor and m["usd"] > m["usd_prev"]:
                growth.append({"principal": m["principal"], "name": m["name"], "team": m["team"],
                               "department": m["department"], "usd": round(m["usd"], 2),
                               "usd_prev": round(m["usd_prev"], 2),
                               "growth": round((m["usd"] - m["usd_prev"]) / m["usd_prev"], 3)})  # fmt: skip
        growth.sort(key=lambda r: -r["growth"])
        return {"cost": cost[:limit], "risk": risk[:limit], "growth": growth[:limit]}

    def cached_snapshot(self, cache, days: int) -> dict:
        d = window(days, time.time())[0]
        return cache(("org-snapshot", d), lambda: self.snapshot(d))


def _team(t: str | None) -> str:
    return "unattributed" if t in (None, "(none)") else t


def _who(m: dict) -> dict[str, Any]:
    return {"principal": m["principal"], "name": m["name"], "team": m["team"], "department": m["department"]}


def register(app: FastAPI, store: PolicyStore, layer: ControlLayer, cached, principal_row, view: OrgView) -> None:
    """`cached(key, fn)` memoizes an aggregate for the TTL; `principal_row(policy, principal_id, team, role)` is
    the per-person row of /admin/principals."""
    usage = layer.usage

    def err(msg: str, code: int = 400) -> JSONResponse:
        return JSONResponse({"error": msg}, status_code=code)

    def snap(days: int) -> dict:
        return view.cached_snapshot(cached, days)

    def totals(s: dict) -> dict[str, Any]:
        ev = list(s["events"].values())
        wf: dict[str, float] = defaultdict(float)
        for w in s["workflows"].values():
            for k, v in w.items():
                wf[k] += v
        return view.metrics(s["people"], ev, wf)

    @app.get("/admin/org")
    async def admin_org(days: int = 30):
        s = snap(days)
        p = store.policy
        depts = view.departments(s)
        t = totals(s)
        teams = {(r["department"], r["name"]) for r in view.teams(s)}
        return {
            "name": p.org.name or p.name,
            "headcount": t["headcount"],
            "active": t["active"],
            "teams": len(teams),
            "departments_count": len(depts),
            "window": {"days": s["days"], "since": s["since"]},
            "totals": {k: v for k, v in t.items() if k not in ("headcount", "active", "top_workflow")},
            "departments": depts,
        }

    def sort_rows(rows: list[dict], sort: str, order: str) -> list[dict]:
        def key(r: dict) -> Any:
            if sort == "risk":
                return (r["people_at_risk"], r["incidents_open"])
            if sort == "name":
                return r["name"].lower()
            v = r.get(sort)
            return -1.0 if v is None else v

        return sorted(rows, key=key, reverse=order != "asc")

    @app.get("/admin/org/teams")
    async def admin_org_teams(
        department: str | None = None,
        q: str = "",
        days: int = 30,
        sort: str = "usd",
        order: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ):
        if sort not in TEAM_SORTS:
            return err(f"sort by one of {list(TEAM_SORTS)}")
        rows = view.teams(snap(days), department or None)
        if q.strip():
            rows = [r for r in rows if q.strip().lower() in r["name"].lower()]
        rows = sort_rows(rows, sort, order or ("asc" if sort == "name" else "desc"))
        limit, offset = max(1, min(limit, 500)), max(0, offset)
        return {"total": len(rows), "rows": rows[offset : offset + limit]}

    def with_prev(rows: list[dict], since: float, days: int, flt: dict) -> list[dict]:
        """Each workflow row with its spend in the window before."""
        prev = {r["workflow"]: r["usd"] or 0.0
                for r in usage.breakdown(["workflow"], since - days * DAY, until=since, **flt)}  # fmt: skip
        return [{**r, "usd_prev": round(prev.get(r["workflow"], 0.0), 2)} for r in rows]

    @app.get("/admin/org/unit")
    async def admin_org_unit(kind: str = "department", name: str = "", days: int = 30):
        if kind not in ("department", "team"):
            return err("kind must be department or team")
        s = snap(days)
        if kind == "department":
            rows = [r for r in view.departments(s) if r["name"] == name]
        else:
            rows = [r for r in view.teams(s) if r["name"] == name]
        if not rows:
            return err(f"no such {kind} {name!r}", 404)
        metrics = rows[0]
        flt = {"department": name} if kind == "department" else {"team": name}
        days_, since, _ = window(days, time.time())

        def build() -> dict[str, Any]:
            spend = usage.timeseries("usd", "workflow", since, **flt)
            trend = usage.adherence("day", since, **flt)
            by_day = {r["key"]: r["adherence"] for r in trend}
            labels = [time.strftime("%Y-%m-%d", time.gmtime(since + i * DAY)) for i in range(days_)]
            return {
                "spend": pivot(spend, labels),
                "adherence_trend": {"days": labels, "values": [by_day.get(d) for d in labels]},
                "by_workflow": with_prev(usage.workflow_runs(since, **flt), since, days_, flt),
                "by_resource": [
                    {
                        "resource": r["resource"],
                        "usd": round(r["usd"] or 0, 2),
                        "tokens": r["tokens"] or 0,
                        "minutes": round(r["minutes"] or 0, 1),
                    }
                    for r in usage.breakdown(["resource"], since, **flt)
                    if r["resource"] != "guard"
                ],  # fmt: skip
            }

        detail = cached(("org-unit", kind, name, days_), build)
        return {
            "kind": kind,
            "name": name,
            "metrics": metrics,
            **detail,
            "teams": view.teams(s, name) if kind == "department" else [],
            "outliers": {k: v for k, v in view.outliers(s, 10, **flt).items() if k in ("cost", "risk")},
        }

    @app.get("/admin/people")
    async def admin_people(
        q: str = "",
        department: str | None = None,
        team: str | None = None,
        status: str | None = None,
        sort: str = "risk",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
        days: int = 30,
    ):
        if sort not in PEOPLE_SORTS:
            return err(f"sort by one of {list(PEOPLE_SORTS)}")
        if status and status not in STATUSES:
            return err(f"status is one of {list(STATUSES)}")
        p = store.policy
        needle = q.strip().lower()
        rows = []
        for m in snap(days)["people"]:
            if department and m["department"] != department or team and m["team"] != team:
                continue
            if needle and not any(needle in (x or "").lower() for x in (m["principal"], m["name"], m["email"])):
                continue
            if status in ("watch", "restricted"):
                if layer.risk.own_level(p, m["principal"]) != status:
                    continue
            elif status:
                pp = p.principal(m["principal"])
                limited = pp.status == "active" and p.budget_scale(m["principal"]) < 1
                if not (limited if status == "limited" else pp.status == status):
                    continue
            rows.append(m)
        if sort == "name":
            rows.sort(key=lambda m: (m["name"].lower(), m["principal"]), reverse=order == "desc")
        elif sort == "risk":
            rows.sort(key=lambda m: (m["risk"], m["open_incidents"], m["usd"]), reverse=order != "asc")
        else:
            rows.sort(key=lambda m: m[sort], reverse=order != "asc")
        limit, offset = max(1, min(limit, 500)), max(0, offset)
        page = []
        for m in rows[offset : offset + limit]:
            row = principal_row(p, m["principal"], m["team"], m["role"])
            page.append({**row, "department": m["department"], "name": m["name"], "email": m["email"],
                         "title": m["title"], "usd": m["usd"], "tokens": m["tokens"]})  # fmt: skip
        return {"total": len(rows), "rows": page}

    @app.get("/admin/outliers")
    async def admin_outliers(days: int = 7, limit: int = 10, department: str | None = None, team: str | None = None):
        return view.outliers(snap(days), max(1, min(limit, 100)), department or None, team or None)


def pivot(rows: list[dict[str, Any]], labels: list[str]) -> dict[str, Any]:
    idx = {d: i for i, d in enumerate(labels)}
    series: dict[str, list[float]] = {}
    for r in rows:
        if r["day"] in idx:
            series.setdefault(r["key"], [0.0] * len(labels))[idx[r["day"]]] += round(r["value"] or 0, 6)
    ordered = dict(sorted(series.items(), key=lambda kv: -sum(kv[1])))
    return {
        "days": labels,
        "series": ordered,
        "totals": [round(sum(v[i] for v in ordered.values()), 6) for i in range(len(labels))],
    }
