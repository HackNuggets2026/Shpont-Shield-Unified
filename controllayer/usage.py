"""Durable usage, lease, incident and approval records (SQLite), so a restart keeps budgets and history.

One row per metered thing: an LLM call, the guard's own model time, a closed resource lease, or a
usage report pushed by an external meter (CI, cloud billing). Everything the dashboards break down
by workflow, task, team, resource or model is a GROUP BY over the `usage` table.
"""

from __future__ import annotations

import json
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
CREATE TABLE IF NOT EXISTS admin_actions (
    ts REAL, actor TEXT, action TEXT, target TEXT, reason TEXT, detail TEXT
);
"""

# Columns added after a table was first created: (table, column, type).
MIGRATIONS = [("usage", "source", "TEXT"), ("usage", "client", "TEXT"), ("requests", "detail", "TEXT")]

GROUPS = {"workflow", "task", "team", "principal", "resource", "model", "day", "source"}


def day_of(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
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
        self.lock = threading.Lock()

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
        }
        self._x(f"INSERT INTO usage ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))

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
        self, by: list[str], since: float, principal: str | None = None, until: float | None = None
    ) -> list[dict[str, Any]]:
        cols = [c for c in by if c in GROUPS]
        if not cols:
            raise ValueError(f"group by one of {sorted(GROUPS)}")
        where, args = "ts>=?", [since]
        if until is not None:
            where += " AND ts<?"
            args.append(until)
        if principal:
            where += " AND principal=?"
            args.append(principal)
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
        rows = self._q(
            "SELECT principal, COALESCE(task, session, request_id) run, SUM(input_tokens+output_tokens) tokens,"
            " SUM(usd) usd, SUM(CASE WHEN unit='minute' THEN quantity ELSE 0 END) minutes"
            " FROM usage WHERE workflow=? AND ts>=? AND metered=1 GROUP BY principal, run",
            (workflow, since),
        )
        usd = [r["usd"] for r in rows]
        tokens = [r["tokens"] for r in rows]
        minutes = [r["minutes"] for r in rows]
        return {
            "runs": len(rows),
            "usd_p50": round(percentile(usd, 0.5), 6),
            "usd_p90": round(percentile(usd, 0.9), 6),
            "tokens_p50": int(percentile(tokens, 0.5)),
            "tokens_p90": int(percentile(tokens, 0.9)),
            "minutes_p50": round(percentile(minutes, 0.5), 1),
            "usd_mean": round(statistics.fmean(usd), 6) if usd else 0.0,
        }

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
            "INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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

    def requests(self, principal: str | None = None, status: str | None = None) -> list[dict]:
        where, args = [], []
        if principal:
            where.append("principal=?")
            args.append(principal)
        if status:
            where.append("status=?")
            args.append(status)
        sql = "SELECT * FROM requests" + (" WHERE " + " AND ".join(where) if where else "")
        rows = self._q(sql + " ORDER BY ts DESC LIMIT 200", tuple(args))
        for r in rows:
            r["detail"] = json.loads(r["detail"]) if r.get("detail") else None
        return rows

    def decide_request(self, rid: str, status: str, actor: str, note: str) -> dict | None:
        self._x(
            "UPDATE requests SET status=?, decided_by=?, decided_at=?, note=? WHERE id=? AND status='pending'",
            (status, actor, time.time(), note, rid),
        )
        rows = self._q("SELECT * FROM requests WHERE id=?", (rid,))
        return rows[0] if rows else None

    # ---- admin actions (who changed what, and who looked at whose content) -------

    def log_admin(self, actor: str, action: str, target: str, reason: str, detail: Any = None) -> None:
        self._x(
            "INSERT INTO admin_actions VALUES (?,?,?,?,?,?)",
            (time.time(), actor, action, target, reason, json.dumps(detail) if detail is not None else None),
        )

    def admin_actions(self, target: str | None = None, limit: int = 100) -> list[dict]:
        if target:
            rows = self._q("SELECT * FROM admin_actions WHERE target=? ORDER BY ts DESC LIMIT ?", (target, limit))
        else:
            rows = self._q("SELECT * FROM admin_actions ORDER BY ts DESC LIMIT ?", (limit,))
        for r in rows:
            r["detail"] = json.loads(r["detail"]) if r["detail"] else None
        return rows
