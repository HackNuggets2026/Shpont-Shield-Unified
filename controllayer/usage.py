"""Durable usage, lease, incident and approval records (SQLite), so a restart keeps budgets and history.

One row per metered thing: an LLM call, the guard's own model time, a closed resource lease, or a
usage report pushed by an external meter (CI, cloud billing). Everything the dashboards break down
by workflow, task, team, resource or model is a GROUP BY over the `usage` table.
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    ts REAL, day TEXT, principal TEXT, team TEXT, workflow TEXT, task TEXT, session TEXT,
    resource TEXT, model TEXT, requests INTEGER, input_tokens INTEGER, output_tokens INTEGER,
    quantity REAL, unit TEXT, usd REAL, compute_seconds REAL, request_id TEXT, metered INTEGER,
    source TEXT, client TEXT
);
CREATE INDEX IF NOT EXISTS usage_day ON usage(day);
CREATE INDEX IF NOT EXISTS usage_run ON usage(principal, workflow, task);
CREATE INDEX IF NOT EXISTS usage_who ON usage(principal, ts);
CREATE TABLE IF NOT EXISTS leases (
    id TEXT PRIMARY KEY, resource TEXT, handle TEXT, server TEXT, principal TEXT, team TEXT, workflow TEXT,
    task TEXT, tool TEXT, started REAL, last_activity REAL, ended REAL, end_reason TEXT, usd REAL, flags TEXT
);
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY, ts REAL, principal TEXT, rule TEXT, severity TEXT, weight REAL,
    detail TEXT, evidence TEXT, status TEXT, updated REAL, note TEXT
);
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY, ts REAL, principal TEXT, kind TEXT, workflow TEXT, scale REAL,
    reason TEXT, status TEXT, decided_by TEXT, decided_at REAL, note TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS events (
    ts REAL, id TEXT, source TEXT, kind TEXT, principal TEXT, team TEXT, client TEXT, session TEXT,
    prompt_id TEXT, task TEXT, workflow TEXT, resource TEXT, urn TEXT, model TEXT, tool TEXT, decision TEXT,
    severity TEXT, usd REAL, tokens INTEGER, request_id TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS events_who ON events(principal, ts);
CREATE INDEX IF NOT EXISTS events_req ON events(request_id);
CREATE INDEX IF NOT EXISTS events_id ON events(id);
CREATE TABLE IF NOT EXISTS metric_baselines (
    key TEXT PRIMARY KEY, value REAL, ts REAL
);
CREATE TABLE IF NOT EXISTS admin_actions (
    ts REAL, actor TEXT, action TEXT, target TEXT, reason TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS people (
    principal TEXT PRIMARY KEY, name TEXT, email TEXT, team TEXT, department TEXT, role TEXT, title TEXT,
    location TEXT, cost_center TEXT
);
"""
# Indexes on columns that older databases gain through MIGRATIONS, so they are created after them.
INDEXES = """
CREATE INDEX IF NOT EXISTS people_unit ON people(department, team);
CREATE INDEX IF NOT EXISTS usage_dept ON usage(department, day);
CREATE INDEX IF NOT EXISTS events_dept ON events(department, ts);
"""
# Pre-aggregated rollups, kept in step on every write (and rebuilt in one pass after bulk loads), so the
# console's aggregates over thousands of people read days, not rows. Keys use '(none)' for a missing value.
ROLLUP_VERSION = 1
ROLLUPS = """
CREATE TABLE IF NOT EXISTS unit_day (
    day TEXT, department TEXT, team TEXT, workflow TEXT, resource TEXT, source TEXT, model TEXT,
    requests INTEGER, tokens INTEGER, usd REAL, minutes REAL, guard_tokens INTEGER,
    PRIMARY KEY (day, department, team, workflow, resource, source, model)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS unit_day_dept ON unit_day(department, day);
CREATE INDEX IF NOT EXISTS unit_day_team ON unit_day(team, day);
CREATE TABLE IF NOT EXISTS person_day (
    principal TEXT, day TEXT, team TEXT, department TEXT, requests INTEGER, tokens INTEGER, usd REAL, cc_usd REAL,
    PRIMARY KEY (principal, day)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS event_day (
    day TEXT, department TEXT, team TEXT, workflow TEXT, source TEXT, kind TEXT, decision TEXT,
    n INTEGER, value REAL, usd REAL, tokens INTEGER,
    PRIMARY KEY (day, department, team, workflow, source, kind, decision)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS event_day_dept ON event_day(department, day);
CREATE INDEX IF NOT EXISTS event_day_team ON event_day(team, day);
CREATE TABLE IF NOT EXISTS runs (
    principal TEXT, workflow TEXT, run TEXT, ts REAL, day TEXT, department TEXT, team TEXT,
    usd REAL, tokens INTEGER, minutes REAL,
    PRIMARY KEY (principal, workflow, run)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS runs_by_wf ON runs(workflow, ts, usd, tokens, minutes);
CREATE INDEX IF NOT EXISTS runs_dept ON runs(department, ts, workflow, usd);
CREATE INDEX IF NOT EXISTS runs_team ON runs(team, ts, workflow, usd);
CREATE INDEX IF NOT EXISTS leases_started ON leases(started);
CREATE INDEX IF NOT EXISTS incidents_ts ON incidents(ts);
CREATE INDEX IF NOT EXISTS requests_ts ON requests(status, ts);
"""
UNIT_KEYS = ("department", "team", "workflow", "resource", "source", "model")  # unit_day, besides day
EVENT_KEYS = ("department", "team", "workflow", "source", "kind", "decision")  # event_day, besides day
REBUILD = """
DELETE FROM unit_day;
INSERT INTO unit_day SELECT day, COALESCE(department, 'Unassigned'), COALESCE(team, '(none)'),
    COALESCE(workflow, '(none)'), COALESCE(resource, '(none)'), COALESCE(source, '(none)'), COALESCE(model, '(none)'),
    SUM(requests), SUM(CASE WHEN metered=1 THEN input_tokens+output_tokens ELSE 0 END),
    SUM(CASE WHEN metered=1 THEN usd ELSE 0 END), SUM(CASE WHEN unit='minute' THEN quantity ELSE 0 END),
    SUM(CASE WHEN metered=0 THEN input_tokens ELSE 0 END)
    FROM usage GROUP BY 1, 2, 3, 4, 5, 6, 7;
DELETE FROM person_day;
INSERT INTO person_day SELECT principal, day, MAX(team), MAX(COALESCE(department, 'Unassigned')), SUM(requests),
    SUM(input_tokens+output_tokens), SUM(usd), SUM(CASE WHEN source='claude_code' THEN usd ELSE 0 END)
    FROM usage WHERE metered=1 GROUP BY principal, day;
DELETE FROM runs;
INSERT INTO runs SELECT principal, COALESCE(workflow, '(none)'), COALESCE(task, session, request_id, ''), MIN(ts),
    MIN(day), MAX(COALESCE(department, 'Unassigned')), MAX(team), SUM(usd), SUM(input_tokens+output_tokens),
    SUM(CASE WHEN unit='minute' THEN quantity ELSE 0 END)
    FROM usage WHERE metered=1 GROUP BY 1, 2, 3;
DELETE FROM event_day;
INSERT INTO event_day SELECT strftime('%Y-%m-%d', ts, 'unixepoch'), COALESCE(department, 'Unassigned'),
    COALESCE(team, '(none)'), COALESCE(workflow, '(none)'), COALESCE(source, '(none)'), COALESCE(kind, '(none)'),
    COALESCE(decision, '(none)'), SUM(COALESCE(n, 1)),
    SUM(CASE WHEN kind LIKE 'metric.%' AND json_valid(detail) THEN COALESCE(json_extract(detail, '$.value'), 0)
        ELSE 0 END), SUM(COALESCE(usd, 0)), SUM(COALESCE(tokens, 0))
    FROM events GROUP BY 1, 2, 3, 4, 5, 6, 7;
"""
UPSERT_UNIT = (
    "INSERT INTO unit_day VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET"
    " requests=requests+excluded.requests,"
    " tokens=tokens+excluded.tokens, usd=usd+excluded.usd, minutes=minutes+excluded.minutes,"
    " guard_tokens=guard_tokens+excluded.guard_tokens"
)
UPSERT_PERSON = (
    "INSERT INTO person_day VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET team=excluded.team,"
    " department=excluded.department, requests=requests+excluded.requests, tokens=tokens+excluded.tokens,"
    " usd=usd+excluded.usd, cc_usd=cc_usd+excluded.cc_usd"
)
UPSERT_RUN = (
    "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET usd=usd+excluded.usd,"
    " tokens=tokens+excluded.tokens, minutes=minutes+excluded.minutes"
)
UPSERT_EVENT = (
    "INSERT INTO event_day VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET n=n+excluded.n,"
    " value=value+excluded.value, usd=usd+excluded.usd, tokens=tokens+excluded.tokens"
)


def _days(since: float, until: float | None) -> tuple[list[str], list[Any]]:
    """The rollup tables' `day` condition for a whole-day window."""
    conds, args = ["day>=?"], [day_of(since)]
    if until is not None:
        conds.append("day<?")
        args.append(day_of(until))
    return conds, args


def _k(v: Any) -> Any:
    return "(none)" if v is None else v


def whole_days(since: float, until: float | None = None) -> bool:
    """A window the daily rollups answer exactly: it starts (and ends) at midnight UTC."""
    return since % 86400 == 0 and (until is None or until % 86400 == 0)


PEOPLE_COLUMNS = ("principal", "name", "email", "team", "department", "role", "title", "location", "cost_center")
UNASSIGNED = "Unassigned"

EVENT_COLUMNS = (
    "ts", "id", "source", "kind", "principal", "team", "client", "session", "prompt_id", "task", "workflow",
    "resource", "urn", "model", "tool", "decision", "severity", "usd", "tokens", "request_id", "detail",
    "department", "n",
)  # fmt: skip
EVENT_FILTERS = {"source", "kind", "principal", "team", "session", "task", "workflow", "decision", "severity",
                 "request_id", "resource", "tool", "model", "department"}  # fmt: skip
USAGE_COLUMNS = (
    "ts", "day", "principal", "team", "workflow", "task", "session", "resource", "model", "requests", "input_tokens",
    "output_tokens", "quantity", "unit", "usd", "compute_seconds", "request_id", "metered", "source", "client",
    "department",
)  # fmt: skip

# Columns added after a table was first created: (table, column, type).
MIGRATIONS = [
    ("usage", "source", "TEXT"),
    ("usage", "client", "TEXT"),
    ("requests", "detail", "TEXT"),
    ("leases", "flagged_at", "REAL"),
    ("usage", "department", "TEXT"),
    ("events", "department", "TEXT"),
    ("incidents", "workflow", "TEXT"),
    ("events", "n", "INTEGER"),  # a rollup row: n checks with the same day, person, kind, workflow, decision
]

GROUPS = {"workflow", "task", "team", "principal", "resource", "model", "day", "source", "department"}
COUNT = "SUM(COALESCE(n, 1))"  # events: a rollup row counts as n


def unit_filter(where: list[str], args: list[Any], department: str | None, team: str | None) -> None:
    """Narrow a query to one department and/or team."""
    if department:
        where.append("department=?")
        args.append(department)
    if team:
        where.append("team=?")
        args.append(team)


def day_window(days: float, now: float | None = None, cap: int = 400) -> float:
    """The start of a window of `days` whole UTC days, today included (what the daily rollups answer)."""
    now = time.time() if now is None else now
    return (now // 86400) * 86400 - (max(1, min(math.ceil(days), cap)) - 1) * 86400


def day_of(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def percentile(xs: list[float], q: float, presorted: bool = False) -> float:
    if not xs:
        return 0.0
    s = xs if presorted else sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


class UsageStore:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")  # WAL + NORMAL: durable across crashes of the process
        self.db.executescript(SCHEMA)
        for table, col, typ in MIGRATIONS:
            cols = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        self.db.executescript(INDEXES)
        self.db.executescript(ROLLUPS)
        self.lock = threading.Lock()
        if self.db.execute("PRAGMA user_version").fetchone()[0] < ROLLUP_VERSION:
            self.rebuild_rollups()
        # The directory, cached: every write looks a person's department up here (O(1), no query).
        self.people: dict[str, dict[str, Any]] = {}
        self.emails: dict[str, str] = {}
        self.team_departments: dict[str, str] = {}  # from the policy's `org:` section
        self.org_name = ""
        self._load_people()

    def rebuild_rollups(self) -> None:
        """Recompute every rollup from the raw tables (after a bulk load, or a database from before them)."""
        with self.lock:
            own = not self.db.in_transaction
            if own:
                self.db.execute("BEGIN")
            for stmt in REBUILD.split(";"):
                if stmt.strip():
                    self.db.execute(stmt)
            self.db.execute(f"PRAGMA user_version={ROLLUP_VERSION}")
            if own:
                self.db.execute("COMMIT")

    def _roll_usage(self, r: dict[str, Any]) -> list[tuple[str, tuple]]:
        metered = bool(r["metered"])
        tokens = (r["input_tokens"] or 0) + (r["output_tokens"] or 0)
        usd = r["usd"] or 0.0
        minutes = (r["quantity"] or 0.0) if r["unit"] == "minute" else 0.0
        dept = r["department"] or UNASSIGNED
        out = [
            (
                UPSERT_UNIT,
                (r["day"], dept, _k(r["team"]), _k(r["workflow"]), _k(r["resource"]), _k(r["source"]),
                 _k(r["model"]), r["requests"] or 0, tokens if metered else 0, usd if metered else 0.0, minutes,
                 0 if metered else r["input_tokens"] or 0),
            )
        ]  # fmt: skip
        if metered:
            cc = usd if r["source"] == "claude_code" else 0.0
            person = (r["principal"], r["day"], r["team"], dept, r["requests"] or 0, tokens, usd, cc)
            out.append((UPSERT_PERSON, person))
            run = r["task"] or r["session"] or r["request_id"] or ""
            out.append((UPSERT_RUN, (r["principal"], _k(r["workflow"]), run, r["ts"], r["day"], dept, r["team"], usd,
                                     tokens, minutes)))  # fmt: skip
        return out

    @staticmethod
    def _roll_event(e: dict[str, Any], detail: Any) -> tuple[str, tuple]:
        value = 0.0
        if str(e.get("kind") or "").startswith("metric.") and isinstance(detail, dict):
            v = detail.get("value")
            value = float(v) if isinstance(v, int | float) and not isinstance(v, bool) else 0.0
        return (
            UPSERT_EVENT,
            (day_of(e["ts"]), e["department"] or UNASSIGNED, _k(e["team"]), _k(e["workflow"]), _k(e["source"]),
             _k(e["kind"]), _k(e["decision"]), e.get("n") or 1, value, e.get("usd") or 0.0, e.get("tokens") or 0),
        )  # fmt: skip

    def _write(self, stmts: list[tuple[str, tuple]]) -> None:
        """Several statements as one transaction (or inside the caller's, e.g. the seeder's)."""
        with self.lock:
            own = not self.db.in_transaction
            if own:
                self.db.execute("BEGIN")
            try:
                for sql, args in stmts:
                    self.db.execute(sql, args)
            except BaseException:
                if own:
                    self.db.execute("ROLLBACK")
                raise
            if own:
                self.db.execute("COMMIT")

    # ---- the directory ------------------------------------------------------------

    def _load_people(self) -> None:
        rows = [dict(r) for r in self.db.execute("SELECT * FROM people").fetchall()]
        self.people = {r["principal"]: r for r in rows}
        self.emails = {r["email"].lower(): r["principal"] for r in rows if r.get("email")}

    def set_org(self, name: str, team_departments: dict[str, str]) -> None:
        self.org_name = name
        self.team_departments = dict(team_departments)

    def department_of(self, principal: str | None, team: str | None = None) -> str:
        """A person's department from the directory, else their team's department, else "Unassigned"."""
        p = self.people.get(principal or "")
        if p and p.get("department"):
            return p["department"]
        return self.team_departments.get(team or (p or {}).get("team") or "", UNASSIGNED)

    def person(self, principal: str) -> dict[str, Any] | None:
        return self.people.get(principal)

    def principal_by_email(self, email: str | None) -> str | None:
        return self.emails.get(email.lower()) if email else None

    def upsert_people(self, rows: list[dict[str, Any]]) -> int:
        """Add or replace directory entries; returns how many changed."""
        changed = []
        for r in rows:
            row = {c: r.get(c) for c in PEOPLE_COLUMNS}
            if self.people.get(row["principal"]) != row:
                changed.append(row)
        if changed:
            with self.lock:
                self.db.executemany(
                    f"INSERT OR REPLACE INTO people ({', '.join(PEOPLE_COLUMNS)})"
                    f" VALUES ({', '.join('?' * len(PEOPLE_COLUMNS))})",
                    [tuple(r.values()) for r in changed],
                )
            for row in changed:
                self.people[row["principal"]] = row
                if row.get("email"):
                    self.emails[row["email"].lower()] = row["principal"]
        return len(changed)

    def directory(self) -> list[dict[str, Any]]:
        return list(self.people.values())

    def _q(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def _x(self, sql: str, args: tuple = ()) -> None:
        with self.lock:
            self.db.execute(sql, args)

    # ---- usage -----------------------------------------------------------------

    def add(
        self,
        *,
        principal: str,
        team: str,
        resource: str,
        workflow: str | None = None,
        task: str | None = None,
        session: str | None = None,
        model: str | None = None,
        requests: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        quantity: float = 0.0,
        unit: str = "",
        usd: float = 0.0,
        compute_seconds: float = 0.0,
        request_id: str | None = None,
        metered: bool = True,
        ts: float | None = None,
        source: str = "gateway",
        client: str | None = None,
    ) -> None:
        ts = ts or time.time()
        row = {
            "ts": ts,
            "day": day_of(ts),
            "principal": principal,
            "team": team,
            "workflow": workflow,
            "task": task,
            "session": session,
            "resource": resource,
            "model": model,
            "requests": requests,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "quantity": quantity,
            "unit": unit,
            "usd": usd,
            "compute_seconds": compute_seconds,
            "request_id": request_id,
            "metered": int(metered),
            "source": source,
            "client": client,
            "department": self.department_of(principal, team),
        }
        insert = f"INSERT INTO usage ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})"
        self._write([(insert, tuple(row.values())), *self._roll_usage(row)])

    def add_many(self, rows: list[dict[str, Any]]) -> None:
        """Bulk usage rows (the seeder): `day` and `department` are filled in when missing."""
        out = []
        for r in rows:
            r = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "quantity": 0.0, "unit": "", "usd": 0.0,
                 "compute_seconds": 0.0, "metered": 1, "source": "gateway", **r}  # fmt: skip
            r.setdefault("day", day_of(r["ts"]))
            r["department"] = r.get("department") or self.department_of(r["principal"], r.get("team"))
            out.append(tuple(r.get(c) for c in USAGE_COLUMNS))
        with self.lock:
            self.db.executemany(
                f"INSERT INTO usage ({', '.join(USAGE_COLUMNS)}) VALUES ({', '.join('?' * len(USAGE_COLUMNS))})", out
            )

    def add_events_many(self, rows: list[dict[str, Any]]) -> None:
        """Bulk events (the seeder), rollup rows included."""
        out = []
        for e in rows:
            e = dict(e)
            if e.get("detail") is not None and not isinstance(e["detail"], str):
                e["detail"] = json.dumps(e["detail"])
            e["department"] = e.get("department") or self.department_of(e.get("principal"), e.get("team"))
            out.append(tuple(e.get(c) for c in EVENT_COLUMNS))
        with self.lock:
            self.db.executemany(
                f"INSERT INTO events ({', '.join(EVENT_COLUMNS)}) VALUES ({', '.join('?' * len(EVENT_COLUMNS))})", out
            )

    def day_rows(self, day: str) -> list[dict[str, Any]]:
        """Metered totals for a day by principal, team and model: what the in-memory ledger is rebuilt from."""
        return self._q(
            "SELECT principal, team, model, SUM(requests) requests, SUM(input_tokens) input_tokens,"
            " SUM(output_tokens) output_tokens, SUM(usd) usd, SUM(compute_seconds) compute_seconds"
            " FROM usage WHERE day=? AND metered=1 GROUP BY principal, team, model",
            (day,),
        )

    def run_totals(self, principal: str, workflow: str, task: str) -> dict[str, float]:
        r = self._q(
            "SELECT COALESCE(SUM(input_tokens+output_tokens),0) tokens, COALESCE(SUM(usd),0) usd FROM usage"
            " WHERE principal=? AND workflow=? AND task=? AND metered=1",
            (principal, workflow, task),
        )[0]
        return {"tokens": r["tokens"], "usd": r["usd"]}

    def workflow_of_task(self, principal: str, task: str) -> str | None:
        """The workflow a task was last labelled with, so usage reported later (CI) joins the same run."""
        r = self._q(
            "SELECT workflow FROM usage WHERE principal=? AND task=? AND workflow IS NOT NULL"
            " AND workflow != 'unlabeled' ORDER BY ts DESC LIMIT 1",
            (principal, task),
        )
        return r[0]["workflow"] if r else None

    def tokens_between(self, principal: str, start: float, end: float) -> int:
        r = self._q(
            "SELECT COALESCE(SUM(input_tokens+output_tokens),0) t FROM usage"
            " WHERE principal=? AND ts>=? AND ts<? AND metered=1",
            (principal, start, end),
        )
        return int(r[0]["t"])

    def breakdown(
        self,
        by: list[str],
        since: float,
        principal: str | None = None,
        until: float | None = None,
        department: str | None = None,
        team: str | None = None,
    ) -> list[dict[str, Any]]:
        cols = [c for c in by if c in GROUPS]
        if not cols:
            raise ValueError(f"group by one of {sorted(GROUPS)}")
        if principal is None and whole_days(since, until) and set(cols) <= {"day", *UNIT_KEYS}:
            conds, args = _days(since, until)
            unit_filter(conds, args, department, team)
            sel = ", ".join(f"{c} {c}" for c in cols)
            return self._q(
                f"SELECT {sel}, SUM(requests) requests, SUM(tokens) tokens, SUM(usd) usd, SUM(minutes) minutes,"
                f" SUM(guard_tokens) guard_tokens FROM unit_day WHERE {' AND '.join(conds)}"
                f" GROUP BY {', '.join(cols)} ORDER BY usd DESC, tokens DESC",
                tuple(args),
            )
        conds, args = ["ts>=?"], [since]
        if until is not None:
            conds.append("ts<?")
            args.append(until)
        if principal:
            conds.append("principal=?")
            args.append(principal)
        unit_filter(conds, args, department, team)
        where = " AND ".join(conds)
        sel = ", ".join(f"COALESCE({c}, '(none)') {c}" for c in cols)
        return self._q(
            f"SELECT {sel}, SUM(requests) requests,"
            " SUM(CASE WHEN metered=1 THEN input_tokens+output_tokens ELSE 0 END) tokens,"
            " SUM(CASE WHEN metered=1 THEN usd ELSE 0 END) usd,"
            " SUM(CASE WHEN unit='minute' THEN quantity ELSE 0 END) minutes,"
            " SUM(CASE WHEN metered=0 THEN input_tokens ELSE 0 END) guard_tokens"
            f" FROM usage WHERE {where} GROUP BY {', '.join(cols)} ORDER BY usd DESC, tokens DESC",
            tuple(args),
        )

    def cost_rows(self, since: float, until: float | None = None) -> list[dict[str, Any]]:
        """Metered usage per day, person, workflow, task and resource: one line per FOCUS cost record."""
        where, args = "ts>=? AND metered=1", [since]
        if until is not None:
            where += " AND ts<?"
            args.append(until)
        return self._q(
            "SELECT day, principal, team, workflow, task, resource, model, unit, COALESCE(source, 'gateway') source,"
            " SUM(requests) requests, SUM(input_tokens+output_tokens) tokens, SUM(quantity) quantity, SUM(usd) usd"
            f" FROM usage WHERE {where} GROUP BY day, principal, team, workflow, task, resource, model, unit, source"
            " ORDER BY day, principal",
            tuple(args),
        )

    def spend(self, since: float, until: float | None = None, team: str | None = None) -> float:
        if whole_days(since, until):
            conds, args = _days(since, until)
            unit_filter(conds, args, None, team)
            q = f"SELECT COALESCE(SUM(usd),0) s FROM unit_day WHERE {' AND '.join(conds)}"
            return float(self._q(q, tuple(args))[0]["s"])
        where, args = "ts>=? AND metered=1", [since]
        if until is not None:
            where += " AND ts<?"
            args.append(until)
        if team:
            where += " AND team=?"
            args.append(team)
        return float(self._q(f"SELECT COALESCE(SUM(usd),0) s FROM usage WHERE {where}", tuple(args))[0]["s"])

    def run_stats(self, workflow: str, since: float) -> dict[str, Any]:
        """The measured price of one run of a workflow. A run is one task id (else session, else call)."""
        with self.lock:  # plain tuples: a popular workflow has 100k+ runs a month
            rows = self.db.execute(
                "SELECT usd, tokens, minutes FROM runs WHERE workflow=? AND ts>=?", (workflow, since)
            ).fetchall()
        usd = sorted(r[0] or 0.0 for r in rows)
        tokens = sorted(r[1] or 0 for r in rows)
        minutes = sorted(r[2] or 0.0 for r in rows)
        return {
            "runs": len(rows),
            "usd_p50": round(percentile(usd, 0.5, True), 6),
            "usd_p90": round(percentile(usd, 0.9, True), 6),
            "tokens_p50": int(percentile(tokens, 0.5, True)),
            "tokens_p90": int(percentile(tokens, 0.9, True)),
            "minutes_p50": round(percentile(minutes, 0.5, True), 1),
            "usd_mean": round(statistics.fmean(usd), 6) if usd else 0.0,
        }

    def workflow_runs(
        self, since: float, principal: str | None = None, department: str | None = None, team: str | None = None
    ) -> list[dict[str, Any]]:
        """Per workflow: spend, runs, and the median and 90th percentile cost of one run."""
        conds, args = ["ts>=?"], [since]
        if principal:
            conds.append("principal=?")
            args.append(principal)
        unit_filter(conds, args, department, team)
        with self.lock:
            rows = self.db.execute(f"SELECT workflow, usd FROM runs WHERE {' AND '.join(conds)}", args).fetchall()
        runs: dict[str, list[float]] = {}
        for wf, usd in rows:
            runs.setdefault(wf, []).append(usd or 0.0)
        out = [
            {"workflow": wf, "usd": round(sum(v), 2), "runs": len(v), "p50": round(percentile(v, 0.5), 4),
             "p90": round(percentile(v, 0.9), 4)}
            for wf, v in runs.items()
        ]  # fmt: skip
        return sorted(out, key=lambda r: -r["usd"])

    # ---- leases ----------------------------------------------------------------

    def save_lease(self, lease: dict[str, Any]) -> None:
        cols = list(lease)
        self._x(
            f"INSERT OR REPLACE INTO leases ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            tuple(json.dumps(v) if isinstance(v, list | dict) else v for v in lease.values()),
        )

    def leases(self, open_only: bool = False, principal: str | None = None, limit: int = 200) -> list[dict]:
        where, args = [], []
        if open_only:
            where.append("ended IS NULL")
        if principal:
            where.append("principal=?")
            args.append(principal)
        sql = "SELECT * FROM leases" + (" WHERE " + " AND ".join(where) if where else "")
        rows = self._q(sql + " ORDER BY started DESC LIMIT ?", (*args, limit))
        for r in rows:
            r["flags"] = json.loads(r["flags"] or "[]")
        return rows

    # ---- incidents ---------------------------------------------------------------

    def add_incident(self, inc: dict[str, Any]) -> None:
        self._x(
            "INSERT INTO incidents (id, ts, principal, rule, severity, weight, detail, evidence, status, updated, note,"
            " workflow) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                inc["id"],
                inc["ts"],
                inc["principal"],
                inc["rule"],
                inc["severity"],
                inc["weight"],
                inc["detail"],
                json.dumps(inc.get("evidence", [])),
                inc.get("status", "open"),
                inc["ts"],
                inc.get("note", ""),
                inc.get("workflow"),
            ),
        )

    def incidents(self, principal: str | None = None, since: float = 0, limit: int = 200) -> list[dict]:
        where, args = "ts>=?", [since]
        if principal:
            where += " AND principal=?"
            args.append(principal)
        rows = self._q(f"SELECT * FROM incidents WHERE {where} ORDER BY ts DESC LIMIT ?", (*args, limit))
        for r in rows:
            r["evidence"] = json.loads(r["evidence"] or "[]")
        return rows

    def set_incident(self, iid: str, status: str, note: str = "") -> bool:
        with self.lock:
            cur = self.db.execute(
                "UPDATE incidents SET status=?, note=?, updated=? WHERE id=?", (status, note, time.time(), iid)
            )
            return cur.rowcount > 0

    # ---- approval requests -------------------------------------------------------

    def add_request(
        self,
        principal: str,
        kind: str,
        workflow: str | None,
        scale: float | None,
        reason: str,
        detail: dict | None = None,
        ts: float | None = None,
    ) -> str:
        rid = uuid.uuid4().hex[:12]
        self._x(
            "INSERT INTO requests (id, ts, principal, kind, workflow, scale, reason, status, decided_by, decided_at,"
            " note, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                ts or time.time(),
                principal,
                kind,
                workflow,
                scale,
                reason,
                "pending",
                None,
                None,
                "",
                json.dumps(detail) if detail else None,
            ),
        )
        return rid

    def requests(
        self,
        principal: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        principals: list[str] | None = None,
        limit: int = 200,
        offset: int = 0,
        oldest_first: bool = False,
    ) -> list[dict]:
        """Newest first (or oldest). `principals` narrows to a set of people (a department); `status` may be
        "decided" (approved or denied)."""
        where, args = self._request_filter(principal, status, kind, principals)
        sql = "SELECT * FROM requests" + (" WHERE " + " AND ".join(where) if where else "")
        order = "ASC" if oldest_first else "DESC"
        rows = self._q(sql + f" ORDER BY ts {order} LIMIT ? OFFSET ?", (*args, limit, offset))
        for r in rows:
            r["detail"] = json.loads(r["detail"]) if r.get("detail") else None
        return rows

    @staticmethod
    def _request_filter(
        principal: str | None, status: str | None, kind: str | None, principals: list[str] | None
    ) -> tuple[list[str], list[Any]]:
        where, args = [], []
        if status == "decided":
            where.append("status IN ('approved', 'denied')")
            status = None
        for col, v in (("principal", principal), ("status", status), ("kind", kind)):
            if v:
                where.append(f"{col}=?")
                args.append(v)
        if principals is not None:
            where.append(f"principal IN ({', '.join('?' * len(principals))})" if principals else "0")
            args.extend(principals)
        return where, args

    def count_requests(
        self, status: str | None = None, kind: str | None = None, principals: list[str] | None = None
    ) -> int:
        where, args = self._request_filter(None, status, kind, principals)
        sql = "SELECT COUNT(*) n FROM requests" + (" WHERE " + " AND ".join(where) if where else "")
        return int(self._q(sql, tuple(args))[0]["n"])

    def in_department(self, department: str) -> list[str]:
        """Everyone the directory places in a department."""
        return [pid for pid in self.people if self.department_of(pid) == department]

    def request(self, rid: str) -> dict | None:
        rows = self._q("SELECT * FROM requests WHERE id=?", (rid,))
        for r in rows:
            r["detail"] = json.loads(r["detail"]) if r.get("detail") else None
        return rows[0] if rows else None

    def decide_request(self, rid: str, status: str, actor: str, note: str, ts: float | None = None) -> dict | None:
        self._x(
            "UPDATE requests SET status=?, decided_by=?, decided_at=?, note=? WHERE id=? AND status='pending'",
            (status, actor, ts or time.time(), note, rid),
        )
        rows = self._q("SELECT * FROM requests WHERE id=?", (rid,))
        return rows[0] if rows else None

    # ---- events: the activity stream ------------------------------------------------

    def add_event(self, evt: dict[str, Any]) -> dict[str, Any]:
        row = {c: evt.get(c) for c in EVENT_COLUMNS}
        row["ts"] = row["ts"] or time.time()
        if not row["team"] and row["principal"] in self.people:
            row["team"] = self.people[row["principal"]]["team"]
        row["department"] = row["department"] or self.department_of(row["principal"], row["team"])
        row["id"] = row["id"] or uuid.uuid4().hex[:16]
        detail = row["detail"]
        row["detail"] = json.dumps(detail) if detail is not None and not isinstance(detail, str) else detail
        insert = f"INSERT INTO events ({', '.join(EVENT_COLUMNS)}) VALUES ({', '.join('?' * len(EVENT_COLUMNS))})"
        self._write([(insert, tuple(row.values())), self._roll_event(row, detail)])
        return {**row, "detail": detail}

    def has_event(self, eid: str, source: str, principal: str, client: str | None) -> bool:
        """An event already recorded under this id from the same producer for the same person."""
        return bool(
            self._q(
                "SELECT 1 FROM events WHERE id=? AND source=? AND principal=? AND client IS ? LIMIT 1",
                (eid, source, principal, client),
            )
        )

    def events(
        self,
        since: float = 0,
        until: float | None = None,
        limit: int = 200,
        before: float | None = None,
        exclude_sources: tuple[str, ...] = (),
        interesting: bool = False,
        **eq: Any,
    ) -> list[dict[str, Any]]:
        """Newest first, never a rollup row (n > 1). `eq` filters on columns (EVENT_FILTERS); a list value matches
        any of its items. `interesting` hides allowed checks and info-level events."""
        where, args = ["ts>=?", "(n IS NULL OR n<=1)"], [since]
        if interesting:
            where.append("NOT (kind LIKE 'check.%' AND decision='allow') AND COALESCE(severity, 'info')!='info'")
        if exclude_sources:
            where.append(f"COALESCE(source, '') NOT IN ({', '.join('?' * len(exclude_sources))})")
            args.extend(exclude_sources)
        if until is not None:
            where.append("ts<?")
            args.append(until)
        if before is not None:
            where.append("ts<?")
            args.append(before)
        for k, v in eq.items():
            if k not in EVENT_FILTERS or v is None:
                continue
            if isinstance(v, list | tuple | set):
                if not v:
                    continue
                where.append(f"{k} IN ({', '.join('?' * len(v))})")
                args.extend(v)
            else:
                where.append(f"{k}=?")
                args.append(v)
        rows = self._q(
            f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT ?", (*args, min(limit, 5000))
        )
        for r in rows:
            r["detail"] = json.loads(r["detail"]) if r["detail"] else None
        return rows

    def event_counts(self, by: str, since: float, until: float | None = None, **eq: Any) -> list[dict[str, Any]]:
        """Events per `by` value and decision: what adherence and the activity charts are computed from."""
        if by not in EVENT_FILTERS | {"day"}:
            raise ValueError(f"group by one of {sorted(EVENT_FILTERS | {'day'})}")
        col = "strftime('%Y-%m-%d', ts, 'unixepoch')" if by == "day" else f"COALESCE({by}, '(none)')"
        where, args = ["ts>=?"], [since]
        if until is not None:
            where.append("ts<?")
            args.append(until)
        for k, v in eq.items():
            if k in EVENT_FILTERS and v is not None:
                where.append(f"{k}=?")
                args.append(v)
        return self._q(
            f"SELECT {col} key, COALESCE(decision, 'none') decision, {COUNT} n, COALESCE(SUM(usd), 0) usd,"
            f" COALESCE(SUM(tokens), 0) tokens FROM events WHERE {' AND '.join(where)}"
            f" GROUP BY key, decision ORDER BY key",
            tuple(args),
        )

    def timeseries(
        self,
        metric: str,
        by: str | None,
        since: float,
        principal: str | None = None,
        exclude_sources: tuple[str, ...] = (),
        department: str | None = None,
        team: str | None = None,
    ) -> list[dict[str, Any]]:
        """Daily totals of usd | tokens (usage ledger) or events (activity stream), split by one column."""
        keys = UNIT_KEYS if metric in ("usd", "tokens") else EVENT_KEYS
        if principal is None and not exclude_sources and whole_days(since) and (by is None or by in keys):
            if metric not in ("usd", "tokens", "events"):
                raise ValueError("metric must be usd, tokens or events")
            conds, args = _days(since, None)
            unit_filter(conds, args, department, team)
            table, val = ("event_day", "SUM(n)") if metric == "events" else ("unit_day", f"SUM({metric})")
            return self._q(
                f"SELECT day, {by or repr('total')} key, COALESCE({val}, 0) value FROM {table}"
                f" WHERE {' AND '.join(conds)} GROUP BY day, key ORDER BY day",
                tuple(args),
            )
        if metric in ("usd", "tokens"):
            if by is not None and by not in GROUPS - {"day"}:
                raise ValueError(f"split usage by one of {sorted(GROUPS - {'day'})}")
            val = "SUM(usd)" if metric == "usd" else "SUM(input_tokens+output_tokens)"
            table, where = "usage", "ts>=? AND metered=1"
        elif metric == "events":
            if by is not None and by not in EVENT_FILTERS:
                raise ValueError(f"split events by one of {sorted(EVENT_FILTERS)}")
            val, table, where = COUNT, "events", "ts>=?"
        else:
            raise ValueError("metric must be usd, tokens or events")
        args: list[Any] = [since]
        if exclude_sources and table == "events":
            where += f" AND COALESCE(source, '') NOT IN ({', '.join('?' * len(exclude_sources))})"
            args.extend(exclude_sources)
        if principal:
            where += " AND principal=?"
            args.append(principal)
        extra: list[str] = []
        unit_filter(extra, args, department, team)
        where = " AND ".join([where, *extra])
        key = f"COALESCE({by}, '(none)')" if by else "'total'"
        return self._q(
            f"SELECT strftime('%Y-%m-%d', ts, 'unixepoch') day, {key} key, COALESCE({val}, 0) value"
            f" FROM {table} WHERE {where} GROUP BY day, key ORDER BY day",
            tuple(args),
        )

    def adherence(
        self,
        by: str | None,
        since: float,
        principal: str | None = None,
        department: str | None = None,
        team: str | None = None,
        until: float | None = None,
    ) -> list[dict[str, Any]]:
        """Policy checks (gateway, MCP, Claude Code hooks) per decision: allow/log adhere; warn/redact/block do not."""
        if by is not None and by not in EVENT_FILTERS | {"day"}:
            raise ValueError(f"group by one of {sorted(EVENT_FILTERS | {'day'})}")
        if principal is None and whole_days(since, until) and (by is None or by in {"day", *EVENT_KEYS}):
            conds, args = _days(since, until)
            conds.append("kind LIKE 'check.%'")
            unit_filter(conds, args, department, team)
            rows = self._q(
                f"SELECT {by or repr('all')} key, decision, SUM(n) n FROM event_day WHERE {' AND '.join(conds)}"
                " GROUP BY key, decision",
                tuple(args),
            )
            return self._adherence_rows(rows)
        col = "strftime('%Y-%m-%d', ts, 'unixepoch')" if by == "day" else f"COALESCE({by}, '(none)')" if by else "'all'"
        conds, args = ["ts>=?", "kind LIKE 'check.%'"], [since]
        if until is not None:
            conds.append("ts<?")
            args.append(until)
        if principal:
            conds.append("principal=?")
            args.append(principal)
        unit_filter(conds, args, department, team)
        rows = self._q(
            f"SELECT {col} key, decision, {COUNT} n FROM events WHERE {' AND '.join(conds)} GROUP BY key, decision",
            tuple(args),
        )
        return self._adherence_rows(rows)

    @staticmethod
    def _adherence_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            o = out.setdefault(r["key"], {"key": r["key"], "total": 0, "allow": 0, "log": 0, "warn": 0,
                                          "redact": 0, "block": 0})  # fmt: skip
            o["total"] += r["n"]
            o[r["decision"] if r["decision"] in o else "allow"] += r["n"]
        for o in out.values():
            o["adherence"] = round((o["allow"] + o["log"]) / o["total"], 4) if o["total"] else None
        return sorted(out.values(), key=lambda o: o["key"])

    VALUE_GROUPS = ("workflow", "principal", "team", "department")

    def value(
        self,
        by: str,
        since: float,
        principal: str | None = None,
        department: str | None = None,
        team: str | None = None,
    ) -> list[dict[str, Any]]:
        """Spend next to what Claude Code reports it produced (commits, PRs, lines, sessions), per `by`.

        usd is all metered spend; claude_code_usd the part Claude Code's telemetry reported. lines_per_usd
        counts lines added."""
        if by not in self.VALUE_GROUPS:
            raise ValueError(f"group by one of {list(self.VALUE_GROUPS)}")
        rolled = principal is None and by != "principal" and whole_days(since)
        conds, args = [], [day_of(since) if rolled else since]
        if principal is not None:
            conds.append("principal=?")
            args.append(principal)
        unit_filter(conds, args, department, team)
        who = "".join(f" AND {c}" for c in conds)
        usage_from, events_from, value = (
            ("unit_day WHERE day>=?", "event_day WHERE day>=?", "SUM(value)")
            if rolled
            else ("usage WHERE ts>=? AND metered=1", "events WHERE ts>=?", "SUM(json_extract(detail, '$.value'))")
        )
        out: dict[str, dict[str, Any]] = {}

        counts = ("commits", "pull_requests", "lines_added", "lines_removed", "sessions")

        def row(key: str) -> dict[str, Any]:
            return out.setdefault(key, {"key": key, "usd": 0.0, "claude_code_usd": 0.0, **dict.fromkeys(counts, 0)})

        for r in self._q(
            f"SELECT COALESCE({by}, '(none)') key, COALESCE(SUM(usd), 0) usd,"
            " COALESCE(SUM(CASE WHEN source='claude_code' THEN usd ELSE 0 END), 0) cc"
            f" FROM {usage_from}{who} GROUP BY key",
            tuple(args),
        ):
            o = row(r["key"])
            o["usd"], o["claude_code_usd"] = round(r["usd"], 6), round(r["cc"], 6)
        metrics = {"metric.commit": "commits", "metric.pull_request": "pull_requests", "metric.session": "sessions"}
        for r in self._q(
            f"SELECT COALESCE({by}, '(none)') key, kind, decision,"
            f" COALESCE({value}, 0) v FROM {events_from}"
            " AND kind IN ('metric.commit', 'metric.pull_request', 'metric.session', 'metric.lines_of_code')"
            f"{who} GROUP BY key, kind, decision",
            tuple(args),
        ):
            o, v = row(r["key"]), r["v"] or 0
            if r["kind"] == "metric.lines_of_code":
                field = {"added": "lines_added", "removed": "lines_removed"}.get(r["decision"] or "")
                if field:
                    o[field] += v
            else:
                o[metrics[r["kind"]]] += v
        for o in out.values():
            for k in counts:
                o[k] = int(o[k]) if float(o[k]).is_integer() else round(o[k], 2)
            o["usd_per_commit"] = round(o["usd"] / o["commits"], 4) if o["commits"] else None
            o["lines_per_usd"] = round(o["lines_added"] / o["usd"], 2) if o["usd"] else None
        return sorted(out.values(), key=lambda o: (-o["usd"], o["key"]))

    # ---- cumulative telemetry baselines (OTLP cumulative sums are differenced against these) ----

    def baseline(self, key: str) -> float | None:
        rows = self._q("SELECT value FROM metric_baselines WHERE key=?", (key,))
        return rows[0]["value"] if rows else None

    def set_baseline(self, key: str, value: float, ts: float | None = None) -> None:
        self._x("INSERT OR REPLACE INTO metric_baselines VALUES (?,?,?)", (key, value, ts or time.time()))

    def prune_baselines(self, before: float) -> None:
        self._x("DELETE FROM metric_baselines WHERE ts<?", (before,))

    # ---- admin actions (who changed what, and who looked at whose content) -------

    def log_admin(
        self, actor: str, action: str, target: str, reason: str, detail: Any = None, ts: float | None = None
    ) -> None:
        """`detail` is stored flat: an overlay patch for one person (`{"principals": {target: {...}}}`) or one
        workflow (`{"menu": {"workflows": {target: {...}}}}`) is stored as its inner `{...}`."""
        if isinstance(detail, dict) and len(detail) == 1:
            inner = detail.get("principals") or (detail.get("menu") or {}).get("workflows")
            if isinstance(inner, dict) and set(inner) == {target}:
                detail = inner[target]
        self._x(
            "INSERT INTO admin_actions VALUES (?,?,?,?,?,?)",
            (ts or time.time(), actor, action, target, reason, json.dumps(detail) if detail is not None else None),
        )

    def admin_actions(self, target: str | list[str] | None = None, limit: int = 100) -> list[dict]:
        """Newest first; `target` may be a list (a person and their incident ids)."""
        if isinstance(target, list):
            if not target:
                return []
            marks = ", ".join("?" * len(target))
            rows = self._q(
                f"SELECT * FROM admin_actions WHERE target IN ({marks}) ORDER BY ts DESC LIMIT ?", (*target, limit)
            )
        elif target:
            rows = self._q("SELECT * FROM admin_actions WHERE target=? ORDER BY ts DESC LIMIT ?", (target, limit))
        else:
            rows = self._q("SELECT * FROM admin_actions ORDER BY ts DESC LIMIT ?", (limit,))
        for r in rows:
            r["detail"] = json.loads(r["detail"]) if r["detail"] else None
        return rows
