"""Spend, budget and risk analytics for the security console.

`UsageHistory` keeps time buckets per principal and item (`model:<name>` or `service:<name>`): daily
for 62 days and hourly for 48 hours, fed by every budget-ledger charge, plus daily decision counts
fed by the audit log. `Analytics` joins it with the people in the policy (teams, agents and their
owners, per-person budgets) and the risk engine. A person's spend includes their agents', and so
does their allowance: the daily caps of the person and their agents.
"""

from __future__ import annotations

import bisect
import calendar
import json
import math
import time
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import resources, services
from .config import Policy
from .types import Context, Principal, Verdict

DAY, HOUR = 86400, 3600
USAGE = ("requests", "tokens", "usd")
DECISIONS = ("decisions", "block", "redact", "warn")
ALL = "*"  # the principal of the organisation-wide rows
LEVEL_RANK = {"normal": 0, "watch": 1, "restricted": 2}
# Budget used, as a fraction of the allowance: the upper edge of each band.
BANDS = (("under", 0.5), ("half", 0.8), ("near", 1.0), ("over", math.inf))


def day_label(bucket: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(bucket * DAY))


def day_bucket(label: str) -> int:
    return calendar.timegm(time.strptime(label, "%Y-%m-%d")) // DAY


class Buckets:
    """One row of `slots` consecutive time buckets, `width` seconds each, per key. A ring: a new
    bucket takes the slot of the bucket `slots` older, which is cleared first."""

    def __init__(self, width: int, slots: int, fields: tuple[str, ...]):
        self.width, self.slots, self.fields = width, slots, fields
        self.index: dict[tuple[str, str], int] = {}
        self.keys: list[tuple[str, str]] = []
        self.data = {f: array("d") for f in fields}
        self.head: int | None = None  # newest bucket held
        self.oldest: int | None = None  # oldest bucket ever written that the ring still holds

    def add(self, key: tuple[str, str], ts: float, values: tuple[float, ...]) -> None:
        b = int(ts // self.width)
        if self.head is None or b > self.head:
            self._advance(b)
        elif b <= self.head - self.slots:
            return  # older than the ring holds
        self.oldest = b if self.oldest is None else max(min(self.oldest, b), self.head - self.slots + 1)
        row = self.index.get(key)
        if row is None:
            row = self.index[key] = len(self.keys)
            self.keys.append(key)
            for arr in self.data.values():
                arr.frombytes(bytes(8 * self.slots))
        i = row * self.slots + b % self.slots
        for f, v in zip(self.fields, values, strict=True):
            self.data[f][i] += v

    def _advance(self, b: int) -> None:
        if self.head is not None:
            for nb in range(self.head + 1, min(b, self.head + self.slots) + 1):
                for arr in self.data.values():
                    arr[nb % self.slots :: self.slots] = array("d", bytes(8 * len(self.keys)))
        self.head = b

    def held(self, first: int, last: int) -> list[int]:
        """The buckets of first..last that the ring still holds."""
        if self.head is None:
            return []
        return list(range(max(first, self.head - self.slots + 1), min(last, self.head) + 1))

    def totals(self, first: int, last: int, f: str) -> list[float]:
        """Per row (in `keys` order), the sum of `f` over buckets first..last."""
        out = [0.0] * len(self.keys)
        arr = self.data[f]
        for b in self.held(first, last):
            out = [a + x for a, x in zip(out, arr[b % self.slots :: self.slots], strict=True)]
        return out

    def series(self, key: tuple[str, str], first: int, last: int, f: str) -> list[float]:
        """`f` for each bucket first..last of one key; zero where nothing is held."""
        row = self.index.get(key)
        held = set(self.held(first, last))
        if row is None:
            return [0.0] * (last - first + 1)
        arr, base = self.data[f], row * self.slots
        return [arr[base + b % self.slots] if b in held else 0.0 for b in range(first, last + 1)]

    def dump(self) -> list[list[Any]]:
        rows = []
        for (a, b), row in self.index.items():
            for bucket in self.held(-(2**62), 2**62):
                vals = [self.data[f][row * self.slots + bucket % self.slots] for f in self.fields]
                if any(vals):
                    rows.append([a, b, bucket, *(round(v, 6) for v in vals)])
        return rows

    def load(self, rows: list[list[Any]]) -> None:
        for a, b, bucket, *vals in sorted(rows, key=lambda r: r[2]):
            self.add((a, b), bucket * self.width, tuple(vals))


class UsageHistory:
    def __init__(self) -> None:
        self.daily = Buckets(DAY, 62, USAGE)
        self.hourly = Buckets(HOUR, 48, USAGE)
        self.decisions = Buckets(DAY, 62, DECISIONS)
        self.scores: dict[str, dict[int, float]] = {}  # principal -> day -> highest risk score seen
        self.last_seen: dict[str, float] = {}
        self.version = 0  # bumped on every change; keys the aggregate cache

    def charge(self, pid: str, item: str, ts: float, requests: float, tokens: float, usd: float) -> None:
        for buckets in (self.daily, self.hourly):
            buckets.add((pid, item), ts, (requests, tokens, usd))
            buckets.add((ALL, item), ts, (requests, tokens, usd))
        self._seen(pid, ts)

    def decision(self, pid: str, ts: float, action: str, count: float = 1.0) -> None:
        values = tuple(count * x for x in (1.0, action == "block", action == "redact", action == "warn"))
        self.decisions.add((pid, ""), ts, values)
        self.decisions.add((ALL, ""), ts, values)
        self._seen(pid, ts)

    def score(self, pid: str, ts: float, score: float) -> None:
        days = self.scores.setdefault(pid, {})
        d = int(ts // DAY)
        days[d] = max(days.get(d, 0.0), round(score, 2))
        for old in [x for x in days if x <= d - self.daily.slots]:
            del days[old]
        self.version += 1

    def _seen(self, pid: str, ts: float) -> None:
        self.last_seen[pid] = max(self.last_seen.get(pid, 0.0), ts)
        self.version += 1

    def dump(self) -> dict[str, Any]:
        return {
            "daily": self.daily.dump(),
            "hourly": self.hourly.dump(),
            "decisions": self.decisions.dump(),
            "scores": {pid: {str(d): s for d, s in days.items()} for pid, days in self.scores.items()},
            "last_seen": self.last_seen,
        }

    def load(self, data: dict[str, Any]) -> None:
        self.daily.load(data.get("daily", []))
        self.hourly.load(data.get("hourly", []))
        self.decisions.load(data.get("decisions", []))
        for pid, days in data.get("scores", {}).items():
            for d, s in days.items():
                self.score(pid, int(d) * DAY, s)
        for pid, ts in data.get("last_seen", {}).items():
            self._seen(pid, ts)


@dataclass
class Person:
    id: str
    name: str
    team: str
    role: str
    kind: str
    owner: str | None
    cap: float | None  # USD per day
    agents: list[str] = field(default_factory=list)

    @property
    def principal(self) -> Principal:
        return Principal(self.id, self.team, self.role, kind=self.kind, owner=self.owner)


@dataclass
class Period:
    first: int
    last: int
    usage: dict[str, dict[str, float]]  # principal -> requests/tokens/usd
    items: dict[str, dict[str, float]]  # principal -> item -> usd
    org_items: dict[str, dict[str, float]]  # item -> requests/tokens/usd, whole organisation
    decisions: dict[str, dict[str, float]]  # principal (and ALL) -> decision counts

    @property
    def days(self) -> int:
        return self.last - self.first + 1


def item_label(item: str) -> dict[str, str]:
    kind, _, name = item.partition(":")
    title = services.SERVICES[name].title if kind == "service" and name in services.SERVICES else name
    return {"key": item, "kind": kind, "name": title}


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def _bins(xs: list[float]) -> list[float]:
    """1-2-5 edges from the largest one <= min(xs) to the smallest one > max(xs); xs all > 0."""
    lo, hi = min(xs), max(xs)
    e = 10.0 ** math.floor(math.log10(lo))
    edges: list[float] = []
    while not edges or edges[-1] <= hi:
        for m in (1, 2, 5):
            v = round(e * m, 10)
            if v <= lo:
                edges = [v]
            elif edges[-1] <= hi:
                edges.append(v)
        e *= 10
    return edges


class Analytics:
    def __init__(self, layer: Any, history_path: Path | None = None):
        self.layer = layer
        self.history = UsageHistory()
        self._people: tuple[str, dict[str, Person]] | None = None
        self._periods: dict[tuple[int, int, int], Period] = {}
        if history_path and history_path.is_file():
            self.load_seed(json.loads(history_path.read_text()))
        layer.ledger.listeners.append(self._on_charge)
        layer.audit.listeners.append(self._on_decision)

    @property
    def policy(self) -> Policy:
        return self.layer.policy

    # ---- feeds ----------------------------------------------------------------------------

    def _on_charge(self, ctx: Context, item: str, charge: Any) -> None:
        self.history.charge(ctx.principal.id, item, time.time(), charge.requests, charge.tokens, charge.usd)

    def _on_decision(self, ctx: Context, v: Verdict, event: dict[str, Any]) -> None:
        if not ctx.principal.authenticated or not ctx.scored:
            return  # anonymous probes and playground runs are nobody's activity
        self.history.decision(ctx.principal.id, v.ts, v.action.value)
        for pid in filter(None, (ctx.principal.id, ctx.principal.owner)):
            self.history.score(pid, v.ts, self.layer.risk.score(self.policy, pid))

    def load_seed(self, data: dict[str, Any]) -> None:
        """A seed file (python -m controllayer.seed): history, risk scores and alerts, recent audit events."""
        self.history.load(data.get("history", {}))
        for pid, (score, at) in data.get("risk_scores", {}).items():
            self.layer.risk.restore_score(pid, score, at)
        self.layer.risk.alerts.extend(data.get("alerts", []))
        self.layer.audit.events.extend(data.get("events", []))

    # ---- people ---------------------------------------------------------------------------

    def people(self) -> dict[str, Person]:
        p = self.policy
        if self._people is None or self._people[0] != p.version:
            out: dict[str, Person] = {}
            for k in p.identity.api_keys.values():
                cap = p.budgets.limits_for(k.principal).usd_per_day
                out[k.principal] = Person(k.principal, k.name or k.principal, k.team, k.role, k.kind, k.owner, cap)
            for person in out.values():
                if person.owner in out:
                    out[person.owner].agents.append(person.id)
            self._people = (p.version, out)
        return self._people[1]

    def allowance(self, person: Person) -> float | None:
        """Daily caps of the person and their agents; None when any of them is uncapped."""
        caps = [person.cap] + [self.people()[a].cap for a in person.agents]
        return None if any(c is None for c in caps) else sum(caps)  # type: ignore[misc]

    def risk(self, person: Person) -> dict[str, Any]:
        r, p = self.layer.risk, self.policy
        return {
            "score": round(r.score(p, person.id), 1),
            "level": r.level(p, person.principal),
            "manual": self.layer.state.watch.get(person.id),
            "signals": len(r.signals(p, person.id)),
        }

    # ---- periods --------------------------------------------------------------------------

    def window(self, period: int, end: str | None = None) -> tuple[int, int]:
        last = day_bucket(end) if end else int(time.time() // DAY)
        return last - max(1, min(period, self.history.daily.slots // 2)) + 1, last

    def period(self, first: int, last: int) -> Period:
        key = (self.history.version, first, last)
        if key not in self._periods:
            self._periods = {key: self._aggregate(first, last)}  # keep only the newest
        return self._periods[key]

    def _aggregate(self, first: int, last: int) -> Period:
        d = self.history.daily
        cols = {f: d.totals(first, last, f) for f in USAGE}
        usage: dict[str, dict[str, float]] = {}
        items: dict[str, dict[str, float]] = {}
        org: dict[str, dict[str, float]] = {}
        for row, (pid, item) in enumerate(d.keys):
            vals = {f: cols[f][row] for f in USAGE}
            if not vals["requests"] and not vals["usd"]:
                continue
            if pid == ALL:
                org[item] = vals
                continue
            u = usage.setdefault(pid, dict.fromkeys(USAGE, 0.0))
            for f in USAGE:
                u[f] += vals[f]
            items.setdefault(pid, {})[item] = vals["usd"]
        dec = self.history.decisions
        dcols = {f: dec.totals(first, last, f) for f in DECISIONS}
        decisions = {
            pid: {f: dcols[f][row] for f in DECISIONS}
            for row, (pid, _) in enumerate(dec.keys)
            if dcols["decisions"][row]
        }
        return Period(first, last, usage, items, org, decisions)

    def rollup(self, pr: Period) -> dict[str, dict[str, Any]]:
        """Per human: own and agents' usage summed, items merged, allowance for the period."""
        people = self.people()
        out = {}
        for person in people.values():
            if person.kind != "human":
                continue
            ids = [person.id, *person.agents]
            usd = sum(pr.usage.get(i, {}).get("usd", 0.0) for i in ids)
            items: dict[str, float] = {}
            for i in ids:
                for item, v in pr.items.get(i, {}).items():
                    items[item] = items.get(item, 0.0) + v
            cap = self.allowance(person)
            dec = [pr.decisions.get(i, {}) for i in ids]
            out[person.id] = {
                "usd": usd,
                "requests": sum(pr.usage.get(i, {}).get("requests", 0.0) for i in ids),
                "items": items,
                "budget": cap * pr.days if cap is not None else None,
                "blocks": sum(x.get("block", 0.0) for x in dec),
                "redacts": sum(x.get("redact", 0.0) for x in dec),
                "last_seen": max((self.history.last_seen.get(i, 0.0) for i in ids), default=0.0) or None,
            }
        return out

    # ---- views ----------------------------------------------------------------------------

    def overview(self, period: int, end: str | None = None) -> dict[str, Any]:
        first, last = self.window(period, end)
        pr = self.period(first, last)
        oldest = self.history.daily.oldest
        prev = self.period(first - pr.days, first - 1) if oldest is not None and oldest <= first - pr.days else None
        people = self.people()
        roll = self.rollup(pr)
        humans = [p for p in people.values() if p.kind == "human"]
        agents = [p for p in people.values() if p.kind == "agent"]
        org_usd = sum(v["usd"] for v in pr.org_items.values())
        org = pr.decisions.get(ALL, {})
        spends = sorted(r["usd"] for r in roll.values() if r["usd"] > 0)

        bands = dict.fromkeys([b for b, _ in BANDS] + ["nocap"], 0)
        for r in roll.values():
            bands[_band(r)] += 1
        risks = {kind: dict.fromkeys(LEVEL_RANK, 0) for kind in ("human", "agent")}
        for person in people.values():
            risks[person.kind][self.layer.risk.level(self.policy, person.principal)] += 1

        caps = [self.allowance(p) for p in humans]
        daily_budget = sum(c for c in caps if c is not None)
        teams: dict[str, dict[str, Any]] = {}
        for person in humans:
            r = roll[person.id]
            t = teams.setdefault(
                person.team, {"team": person.team, "people": 0, "active": 0, "usd": 0.0, "budget": 0.0, "blocks": 0.0}
            )
            t["people"] += 1
            t["active"] += r["usd"] > 0 or r["requests"] > 0
            t["usd"] += r["usd"]
            t["budget"] += r["budget"] or 0.0
            t["blocks"] += r["blocks"]

        edges = _bins(spends) if spends else []
        hist = [
            {"lo": lo, "hi": hi, "count": bisect.bisect_left(spends, hi) - bisect.bisect_left(spends, lo)}
            for lo, hi in zip(edges, edges[1:], strict=False)
        ]
        top = sorted(roll.items(), key=lambda kv: -kv[1]["usd"])[:10]
        return {
            "period": pr.days,
            "first": day_label(first),
            "last": day_label(last),
            "totals": {
                "usd": org_usd,
                "usd_models": sum(v["usd"] for k, v in pr.org_items.items() if k.startswith("model:")),
                "usd_services": sum(v["usd"] for k, v in pr.org_items.items() if k.startswith("service:")),
                "prev_usd": sum(v["usd"] for v in prev.org_items.values()) if prev else None,
                "budget": daily_budget * pr.days,
                "people": len(humans),
                "agents": len(agents),
                "active": sum(1 for r in roll.values() if r["usd"] > 0 or r["requests"] > 0),
                "decisions": org.get("decisions", 0),
                "block": org.get("block", 0),
                "redact": org.get("redact", 0),
                "warn": org.get("warn", 0),
                "watched": risks["human"]["watch"] + risks["human"]["restricted"],
            },
            "series": self.org_series(first, last, period == 1 and end is None),
            "daily_budget": daily_budget,
            "items": sorted(({**item_label(k), **v} for k, v in pr.org_items.items()), key=lambda r: -r["usd"]),
            "spend": {
                "histogram": hist,
                "p50": _pct(spends, 0.5),
                "p90": _pct(spends, 0.9),
                "p99": _pct(spends, 0.99),
                "top": [
                    {"id": pid, "name": people[pid].name, "team": people[pid].team, "usd": r["usd"]} for pid, r in top
                ],
            },
            "budget_bands": bands,
            "risk": {
                "people": risks["human"],
                "agents": risks["agent"],
                "overrides": len(self.layer.state.watch),
                "signals": sum(1 for pid in self.layer.state.signals if self.layer.risk.signals(self.policy, pid)),
            },
            "alerts": [_alert(a, people) for a in list(reversed(self.layer.risk.alerts))[:8]],
            "teams": sorted(teams.values(), key=lambda t: -t["usd"]),
        }

    def org_series(self, first: int, last: int, hourly: bool) -> dict[str, Any]:
        """Spend on models and on services per bucket: daily, or the last 24 hours."""
        if hourly:
            b, last_b = self.history.hourly, int(time.time() // HOUR)
            first_b = last_b - 23
        else:
            b, first_b, last_b = self.history.daily, first, last
        out = {"unit": "hour" if hourly else "day", "start": first_b * b.width, "models": [], "services": []}
        zero = [0.0] * (last_b - first_b + 1)
        models, svcs = list(zero), list(zero)
        for pid, item in b.keys:
            if pid != ALL:
                continue
            s = b.series((pid, item), first_b, last_b, "usd")
            acc = models if item.startswith("model:") else svcs
            for i, v in enumerate(s):
                acc[i] += v
        out["models"], out["services"] = models, svcs
        return out

    def people_page(
        self,
        q: str = "",
        team: str = "",
        risk: str = "",
        budget: str = "",
        item: str = "",
        kind: str = "human",
        min_usd: float | None = None,
        max_usd: float | None = None,
        sort: str = "-usd",
        page: int = 1,
        per_page: int = 50,
        period: int = 30,
        end: str | None = None,
    ) -> dict[str, Any]:
        first, last = self.window(period, end)
        pr = self.period(first, last)
        people = self.people()
        roll = self.rollup(pr) if kind == "human" else None
        rows = []
        bands = dict.fromkeys([b for b, _ in BANDS] + ["nocap"], 0)
        levels = dict.fromkeys(LEVEL_RANK, 0)
        ql = q.lower()
        for person in people.values():
            if person.kind != kind or (team and person.team != team):
                continue
            if ql and ql not in person.id.lower() and ql not in person.name.lower() and ql not in person.team.lower():
                continue
            if roll is not None:
                r = roll[person.id]
            else:
                u, cap = pr.usage.get(person.id, {}), person.cap
                r = {
                    "usd": u.get("usd", 0.0),
                    "requests": u.get("requests", 0.0),
                    "items": pr.items.get(person.id, {}),
                    "budget": cap * pr.days if cap is not None else None,
                    "blocks": pr.decisions.get(person.id, {}).get("block", 0.0),
                    "redacts": pr.decisions.get(person.id, {}).get("redact", 0.0),
                    "last_seen": self.history.last_seen.get(person.id),
                }
            if item and item not in r["items"]:
                continue
            if (min_usd is not None and r["usd"] < min_usd) or (max_usd is not None and r["usd"] >= max_usd):
                continue
            band, rk = _band(r), self.risk(person)
            band_ok = not budget or band == budget
            risk_ok = not (
                (risk == "elevated" and rk["level"] == "normal")
                or (risk in LEVEL_RANK and rk["level"] != risk)
                or (risk == "flagged" and not (rk["score"] > 0 or rk["manual"] or rk["signals"]))
            )
            # Each facet counts with every other filter applied, so a chip shows what picking it would give.
            if risk_ok:
                bands[band] += 1
            if band_ok:
                levels[rk["level"]] += 1
            if not (band_ok and risk_ok):
                continue
            top = max(r["items"].items(), key=lambda kv: kv[1], default=None)
            rows.append(
                {
                    "id": person.id,
                    "name": person.name,
                    "team": person.team,
                    "role": person.role,
                    "owner": person.owner,
                    "agents": len(person.agents),
                    "usd": r["usd"],
                    "requests": r["requests"],
                    "budget": r["budget"],
                    "used": r["usd"] / r["budget"] if r["budget"] else None,
                    "top_item": {**item_label(top[0]), "usd": top[1]} if top else None,
                    "item_usd": r["items"].get(item) if item else None,
                    "blocks": r["blocks"],
                    "redacts": r["redacts"],
                    "last_seen": r["last_seen"],
                    **rk,
                }
            )
        desc = sort.startswith("-")
        col = sort.lstrip("-")
        keyf = SORTS.get(col, SORTS["usd"])
        rows.sort(key=lambda r: r["name"].lower())
        rows.sort(key=keyf, reverse=desc)
        per_page = max(1, min(per_page, 200))
        pages = max(1, math.ceil(len(rows) / per_page))
        page = max(1, min(page, pages))
        teams = sorted({p.team for p in people.values() if p.kind == kind})
        return {
            "total": len(rows),
            "page": page,
            "pages": pages,
            "per_page": per_page,
            "period": pr.days,
            "first": day_label(first),
            "last": day_label(last),
            "sort": ("-" if desc else "") + (col if col in SORTS else "usd"),
            "teams": teams,
            "summary": {
                "usd": sum(r["usd"] for r in rows),
                "budget": sum(r["budget"] for r in rows if r["budget"] is not None),
                "blocks": sum(r["blocks"] for r in rows),
                "bands": bands,
                "levels": levels,
            },
            "items": sorted(
                ({**item_label(k), "usd": v["usd"]} for k, v in pr.org_items.items()), key=lambda i: -i["usd"]
            ),
            "rows": rows[(page - 1) * per_page : page * per_page],
        }

    def person(self, pid: str, period: int = 30, end: str | None = None) -> dict[str, Any] | None:
        people = self.people()
        person = people.get(pid)
        if person is None:
            return None
        first, last = self.window(period, end)
        pr = self.period(first, last)
        ids = [person.id, *person.agents]
        hourly = period == 1 and end is None
        b = self.history.hourly if hourly else self.history.daily
        last_b = int(time.time() // HOUR) if hourly else last
        first_b = last_b - 23 if hourly else first

        by_item: dict[str, dict[str, Any]] = {}
        for i in ids:
            for item, usd in pr.items.get(i, {}).items():
                row = by_item.setdefault(item, {**item_label(item), "usd": 0.0, "by_principal": {}})
                row["usd"] += usd
                row["by_principal"][i] = usd
        ranked = sorted(by_item.values(), key=lambda r: -r["usd"])
        shown = [r["key"] for r in ranked[:5]]
        series = {k: [0.0] * (last_b - first_b + 1) for k in [*shown, "other"]}
        for i in ids:
            for item in by_item:
                s = b.series((i, item), first_b, last_b, "usd")
                acc = series[item if item in shown else "other"]
                for n, v in enumerate(s):
                    acc[n] += v
        if not any(series["other"]):
            del series["other"]

        cap = self.allowance(person)
        today = int(time.time() // DAY)
        daily = self.history.daily
        today_usd = sum(daily.series(k, today, today, "usd")[0] for k in daily.keys if k[0] in ids)
        usd = sum(r["usd"] for r in ranked)
        dec = [pr.decisions.get(i, {}) for i in ids]
        r = self.layer.risk
        p = self.policy
        score_days = self.history.scores.get(pid, {})
        history = [score_days.get(d, 0.0) for d in range(first, last + 1)]
        if last == today:
            history[-1] = max(history[-1], round(r.score(p, pid), 2))
        mine = set(ids)
        return {
            "person": {
                "id": person.id,
                "name": person.name,
                "team": person.team,
                "role": person.role,
                "kind": person.kind,
                "owner": person.owner,
                "owner_name": people[person.owner].name if person.owner in people else None,
                "cap": person.cap,
            },
            "period": pr.days,
            "first": day_label(first),
            "last": day_label(last),
            "spend": {
                "usd": usd,
                "requests": sum(pr.usage.get(i, {}).get("requests", 0.0) for i in ids),
                "tokens": sum(pr.usage.get(i, {}).get("tokens", 0.0) for i in ids),
                "budget": cap * pr.days if cap is not None else None,
                "daily_cap": cap,
                "today_usd": today_usd,
                "today_left": max(0.0, cap - today_usd) if cap is not None else None,
            },
            "decisions": {f: sum(x.get(f, 0.0) for x in dec) for f in DECISIONS},
            "series": {
                "unit": "hour" if hourly else "day",
                "start": first_b * b.width,
                "items": [
                    item_label(k) if k != "other" else {"key": "other", "kind": "", "name": "Other"} for k in series
                ],
                "values": list(series.values()),
            },
            "items": ranked,
            "agents": [self._agent(people[a], pr) for a in ([person.id] if person.kind == "agent" else person.agents)],
            "resources": self._entitled(person),
            "risk": {
                **self.risk(person),
                "auto": r.level(p, person.principal, manual=False),
                "computed": r.computed_level(p, pid),
                "signals": [{"source": s, **sig} for s, sig in sorted(r.signals(p, pid).items())],
                "history": history,
                "levels": p.insider_risk.levels.model_dump(),
            },
            "alerts": [_alert(a, people) for a in reversed(r.alerts) if a["principal"] in mine][:20],
            "events": [e for e in reversed(self.layer.audit.events) if e.get("principal") in mine][:25],
        }

    def _agent(self, agent: Person, pr: Period) -> dict[str, Any]:
        p, state = self.policy, self.layer.state
        grants = {}
        for rid, g in state.grants.get(agent.id, {}).items():
            grants[rid] = {
                "scopes": g["scopes"],
                "expires_at": g["expires_at"],
                "active": resources.active_grant(p, state, agent.principal, rid) is not None,
            }
        return {
            "id": agent.id,
            "name": agent.name,
            "usd": pr.usage.get(agent.id, {}).get("usd", 0.0),
            "cap": agent.cap,
            "level": self.layer.risk.level(p, agent.principal),
            "grants": grants,
        }

    def _entitled(self, person: Person) -> list[dict[str, Any]]:
        p, state = self.policy, self.layer.state
        scopes = resources.entitled_scopes(p, person.principal)
        return [
            {
                "id": rid,
                "name": r.name,
                "type": r.type,
                "sensitivity": r.sensitivity,
                "scopes": scopes[rid],
                "max_grant_hours": r.max_grant_hours,
                "suspended": resources.suspended(p, state, rid),
            }
            for rid, r in resources.entitled(p, person.principal).items()
        ]

    def resources(self, period: int, end: str | None = None) -> dict[str, Any]:
        """Per catalog resource: what its calls cost and how many live grants it has."""
        first, last = self.window(period, end)
        pr = self.period(first, last)
        p, state = self.policy, self.layer.state
        people = self.people()
        grants: dict[str, list[dict[str, Any]]] = {}
        for agent, gs in state.grants.items():
            a = people.get(agent)
            for rid, g in gs.items():
                active = a is not None and resources.active_grant(p, state, a.principal, rid) is not None
                grants.setdefault(rid, []).append(
                    {"agent": agent, "owner": a.owner if a else None, **g, "active": active}
                )
        out = []
        for rid, r in p.resources.items():
            svc = r.connection.get("service") if r.type == "service" else None
            usage = pr.org_items.get(f"service:{svc}", {}) if svc else {}
            price = p.budgets.services.get(svc) if svc else None
            out.append(
                {
                    "id": rid,
                    "name": r.name,
                    "type": r.type,
                    "service": svc,
                    "sensitivity": r.sensitivity,
                    "scopes": r.scopes,
                    "suspended": resources.suspended(p, state, rid),
                    "suspended_in_policy": r.suspended,
                    "usd_per_call": price.usd_per_call if price else None,
                    "calls": usage.get("requests", 0.0),
                    "usd": usage.get("usd", 0.0),
                    "grants": sorted(grants.get(rid, []), key=lambda g: (not g["active"], g["agent"])),
                }
            )
        return {"period": pr.days, "resources": sorted(out, key=lambda r: -r["usd"])}


def _band(r: dict[str, Any]) -> str:
    if not r["budget"]:
        return "nocap"
    used = r["usd"] / r["budget"]
    return next(name for name, edge in BANDS if used < edge)


def _alert(a: dict[str, Any], people: dict[str, Person]) -> dict[str, Any]:
    who = people.get(a["principal"])
    return {**a, "name": who.name if who else a["principal"]}


SORTS = {
    "usd": lambda r: r["usd"],
    "used": lambda r: -1.0 if r["used"] is None else r["used"],
    "name": lambda r: r["name"].lower(),
    "team": lambda r: r["team"],
    "risk": lambda r: (LEVEL_RANK[r["level"]], r["score"]),
    "score": lambda r: r["score"],
    "last": lambda r: r["last_seen"] or 0.0,
    "item": lambda r: r["item_usd"] or 0.0,
    "blocks": lambda r: r["blocks"],
}
