"""The rest of ACME Bank: a few thousand people in 6 departments and 41 teams, written as daily rollups.

The 12 named demo people (seed/world.py) keep their call-by-call history. Everyone else gets one usage row per
run (gateway) or session (Claude Code) and, per day, rollup events (`events.n`): n allowed checks with the same
day, person, kind, workflow and decision. Interventions (blocks, redactions, warnings), incidents, zombie flags
and grants stay single events, so the activity feed shows them.

Spend is driven by department profiles: engineering is heavy on Claude Code, data and finance run analyses on
GPT-4o, sales and operations are light chat users, with a long tail of heavy users everywhere.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Any

from controllayer.detections import severity as incident_severity
from controllayer.usage import UsageStore, day_of

DAY = 86400.0
PRICES = {"gpt-4o": (2.5e-6, 10e-6), "gpt-4o-mini": (0.15e-6, 0.6e-6)}
HOURS = {"London": (8, 18), "Warsaw": (7, 17), "Frankfurt": (7, 17), "New York": (13, 23), "Singapore": (1, 10)}

# workflow -> (calls per run, models, input tokens per call, output tokens per call, tool calls per call)
RUNS: dict[str, tuple[tuple[int, int], tuple[str, ...], tuple[int, int], tuple[int, int], float]] = {
    "chat_assist": ((1, 4), ("gpt-4o-mini", "gpt-4o"), (1500, 12000), (200, 1200), 0.0),
    "pr_review": ((2, 6), ("gpt-4o",), (4000, 15000), (500, 2000), 1.2),
    "bugfix": ((3, 9), ("gpt-4o",), (3000, 12000), (500, 3000), 1.0),
    "ui_qa": ((2, 5), ("gpt-4o-mini",), (800, 3000), (200, 800), 1.5),
    "data_analysis": ((2, 7), ("gpt-4o",), (5000, 20000), (800, 2500), 0.8),
    "load_test": ((1, 3), ("gpt-4o-mini",), (500, 2000), (200, 600), 1.0),
    "unlabeled": ((1, 3), ("gpt-4o",), (2000, 10000), (300, 1500), 0.0),
}
PREFIX = {"pr_review": "PR", "bugfix": "BUG", "ui_qa": "QA", "data_analysis": "FIN", "load_test": "LT",
          "chat_assist": "Q", "unlabeled": "Q"}  # fmt: skip
TOOLS = {"pr_review": "read_file", "bugfix": "run_tests", "ui_qa": "simulator_tap", "data_analysis": "read_file",
         "load_test": "run_tests"}  # fmt: skip

# (decision, severity, finding): interventions on single gateway checks
FINDINGS = {
    "email": ("redact", "low", "pii/email:redact"),
    "phone": ("redact", "low", "pii/phone:redact"),
    "card": ("block", "high", "pii/credit_card:block"),
    "iban": ("redact", "low", "pii/iban:redact"),
    "aws": ("block", "high", "secrets/aws_access_key:block"),
    "github": ("block", "high", "secrets/github_token:block"),
    "password": ("redact", "low", "secrets/password_assignment:redact"),
    "injection": ("block", "high", "prompt_injection/prompt_injection:block"),
    "confidential": ("warn", "low", "confidential_output/confidential:warn"),
    "off_topic": ("log", "info", "off_policy_use/off_policy_use:log"),
}

FIRST = (
    "Adam Agnieszka Ahmed Aisha Alex Amelia Ana Andrei Anna Arjun Ben Camille Carlos Chen Chloe Daniel David Diana "
    "Elena Emma Ethan Fatima Felix Freya George Hana Hannah Hugo Ines Isaac Jakub James Jan Jia Julia Kai Karol "
    "Kasia Kenji Laura Leo Lena Liam Lucas Lucy Maja Marco Maria Marta Mateo Mei Michal Mia Mohammed Nadia Nikos "
    "Noah Nora Olivia Omar Oscar Pablo Paula Piotr Priya Rafael Rahul Rosa Ruth Sam Sara Sofia Sven Tariq Tomasz "
    "Ula Victor Wei Wiktoria Yara Yusuf Zofia Zoe Aditya Bianca Cyrus Dario Emeka Ewa Farah Gosia Ivo Jonas"
).split()
LAST = (
    "Abbott Adeyemi Alvarez Andersen Bakker Banerjee Becker Bianchi Brennan Byrne Carter Chowdhury Costa Dabrowski "
    "Delgado Dubois Eriksen Ferreira Fischer Fontaine Garcia Gonzalez Grabowski Hansen Hartmann Hughes Ivanova "
    "Jensen Kaminski Kapoor Keller Kowalczyk Kowalski Kumar Lambert Larsen Lewandowski Lindgren Lopez Marino Martin "
    "Mazur Meyer Moreno Murphy Nakamura Nguyen Nowak Okafor Olsen Oyelaran Park Patel Perez Petersen Quinn Rahman "
    "Reddy Ricci Rossi Sanchez Santos Schmidt Schneider Silva Singh Sokolova Suzuki Szymanski Tanaka Thompson Torres "
    "Varga Wagner Walsh Wang Wieczorek Wilson Wojcik Wong Yamamoto Zielinski Zhang Novak Horvat Kovacs Brandt Moretti"
).split()


@dataclass
class Dept:
    name: str
    code: str
    people: int
    teams: list[str]
    dormant: float  # share with no AI usage at all in the window
    cc: float  # Claude Code adoption among the active
    cc_month: float  # median Claude Code $/month of a user
    runs: dict[str, float]  # gateway workflow -> runs per working day (median person)
    ctx: float  # context size multiplier (pasted documents)
    intervene: float  # chance one gateway call needs an intervention
    findings: dict[str, float]  # FINDINGS key -> weight
    titles: list[tuple[str, str, float]]  # role, title, weight
    locations: dict[str, float]
    lease_teams: tuple[str, ...] = ()  # teams that drive simulators (ui_qa)


DEPARTMENTS = [
    Dept("Engineering", "ENG", 1800,
         ["engineering", "payments", "mobile", "web", "data-platform", "sre", "core-banking", "cards", "lending",
          "identity", "api-gateway", "devex", "qa-automation", "fraud-engineering"],
         dormant=0.04, cc=0.72, cc_month=185, runs={"chat_assist": 1.2, "pr_review": 0.6, "bugfix": 0.5},
         ctx=1.0, intervene=0.016, findings={"aws": 3, "github": 3, "password": 3, "email": 2, "injection": 1,
                                             "confidential": 1, "off_topic": 1},
         titles=[("developer", "Software Engineer", 5), ("developer", "Senior Software Engineer", 4),
                 ("developer", "Staff Engineer", 1), ("manager", "Engineering Manager", 1),
                 ("developer", "QA Engineer", 1)],
         locations={"Warsaw": 4, "London": 3, "New York": 2, "Singapore": 1},
         lease_teams=("mobile", "web", "qa-automation")),
    Dept("Platform & Security", "PLS", 450, ["platform", "security", "it-ops", "cloud-infra", "network"],
         dormant=0.08, cc=0.5, cc_month=190, runs={"chat_assist": 1.4, "bugfix": 0.5, "pr_review": 0.4},
         ctx=1.0, intervene=0.012, findings={"aws": 3, "password": 3, "github": 2, "email": 1, "off_topic": 1},
         titles=[("developer", "Platform Engineer", 4), ("developer", "Security Engineer", 2),
                 ("developer", "Site Reliability Engineer", 2), ("manager", "Platform Lead", 1)],
         locations={"London": 3, "Warsaw": 3, "Frankfurt": 1}),
    Dept("Data & Analytics", "DAT", 550, ["analytics", "ml", "bi", "data-governance", "research"],
         dormant=0.1, cc=0.35, cc_month=150, runs={"data_analysis": 1.6, "chat_assist": 1.4, "unlabeled": 0.3},
         ctx=1.2, intervene=0.022, findings={"email": 4, "iban": 2, "card": 1, "confidential": 3, "password": 1},
         titles=[("analyst", "Data Analyst", 4), ("developer", "Data Scientist", 3), ("developer", "ML Engineer", 2),
                 ("manager", "Analytics Manager", 1)],
         locations={"London": 3, "Warsaw": 2, "New York": 2}),
    Dept("Finance", "FIN", 600, ["finance", "treasury", "risk", "accounting", "tax"],
         dormant=0.22, cc=0.03, cc_month=80, runs={"data_analysis": 1.0, "chat_assist": 1.6, "unlabeled": 0.6},
         ctx=1.3, intervene=0.03, findings={"card": 3, "iban": 4, "email": 3, "confidential": 3, "off_topic": 1},
         titles=[("analyst", "Financial Analyst", 5), ("analyst", "Risk Analyst", 2), ("analyst", "Accountant", 3),
                 ("manager", "Finance Manager", 1)],
         locations={"London": 4, "Frankfurt": 2, "New York": 2, "Singapore": 1}),
    Dept("Operations", "OPS", 900, ["customer-ops", "compliance", "procurement", "legal", "hr", "facilities"],
         dormant=0.38, cc=0.02, cc_month=60, runs={"chat_assist": 2.0, "unlabeled": 1.1},
         ctx=1.4, intervene=0.035, findings={"email": 6, "phone": 4, "iban": 2, "confidential": 2, "off_topic": 2,
                                             "injection": 1},
         titles=[("analyst", "Operations Specialist", 5), ("analyst", "Compliance Officer", 2),
                 ("analyst", "HR Partner", 1), ("manager", "Operations Manager", 1)],
         locations={"Warsaw": 4, "London": 2, "Singapore": 1}),
    Dept("Sales & Support", "SAL", 700, ["sales-emea", "sales-amer", "sales-apac", "support", "marketing"],
         dormant=0.33, cc=0.01, cc_month=60, runs={"chat_assist": 2.2, "unlabeled": 1.2},
         ctx=1.3, intervene=0.045, findings={"email": 7, "phone": 5, "card": 1, "injection": 2, "off_topic": 3,
                                            "confidential": 1},
         titles=[("analyst", "Account Executive", 4), ("analyst", "Support Specialist", 4),
                 ("analyst", "Marketing Manager", 1), ("manager", "Sales Director", 1)],
         locations={"London": 3, "New York": 3, "Singapore": 2, "Frankfurt": 1}),
]  # fmt: skip


@dataclass(slots=True)
class Member:
    id: str
    name: str
    team: str
    dept: Dept
    role: str
    hours: tuple[int, int]
    active: bool
    gateway: float  # multiplier on the department's gateway run rates
    cc_month: float  # 0: no Claude Code
    workflows: dict[str, float] = field(default_factory=dict)
    # filled while generating: daily averages for the earlier window
    usd_gw: float = 0.0
    usd_cc: float = 0.0
    tok_gw: int = 0
    tok_cc: int = 0
    days: int = 0


def pick(rng: random.Random, weights: dict[str, float]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(rng.gauss(lam, math.sqrt(lam)) + 0.5))
    k, prod, limit = 0, rng.random(), math.exp(-lam)
    while prod > limit:
        k += 1
        prod *= rng.random()
    return k


class OrgSeeder:
    """Writes the directory and the rollup history of everyone but the named demo people."""

    def __init__(self, store: UsageStore, rng: random.Random, now: float, days: int, people: int, named: list[dict]):
        self.store, self.rng, self.now, self.days = store, rng, now, days
        self.members: list[Member] = []
        self.usage: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.leases: list[dict[str, Any]] = []
        self.stats = {"people": 0, "events": 0, "leases": 0}
        self._ids: set[str] = {p["principal"] for p in named}
        self.named = named
        self.build(people)

    def id(self) -> str:
        return f"{self.rng.getrandbits(64):016x}"

    # ---- the directory ----------------------------------------------------------------

    def build(self, people: int) -> None:
        rng = self.rng
        total = sum(d.people for d in DEPARTMENTS)
        rows = []
        for d in DEPARTMENTS:
            n = round(d.people * people / total)
            weights = [rng.uniform(0.7, 1.3) for _ in d.teams]
            locs = list(d.locations)
            for i in range(n):
                team = rng.choices(d.teams, weights=weights)[0]
                first, last = rng.choice(FIRST), rng.choice(LAST)
                base = (first[0] + last).lower()
                pid, k = base, 1
                while pid in self._ids:
                    k += 1
                    pid = f"{base}{k}"
                self._ids.add(pid)
                role, title = rng.choices([(r, t) for r, t, _ in d.titles], weights=[w for *_, w in d.titles])[0]
                if i < len(d.teams):  # one manager per team, first in line
                    team, role, title = (
                        d.teams[i],
                        "manager",
                        d.titles[-1][1] if d.titles[-1][0] == "manager" else title,
                    )
                loc = rng.choices(locs, weights=list(d.locations.values()))[0]
                active = rng.random() >= d.dormant
                heavy = 4.0 if rng.random() < 0.03 else 1.0  # the long tail
                cc = 0.0
                if active and rng.random() < d.cc:
                    cc = d.cc_month * math.exp(rng.gauss(0, 0.45)) * heavy
                wfs = dict(d.runs)
                if team in d.lease_teams:
                    wfs["ui_qa"] = 0.7
                if team in ("sre", "qa-automation", "platform", "payments", "core-banking") and rng.random() < 0.3:
                    wfs["load_test"] = 0.15
                m = Member(pid, f"{first} {last}", team, d, role, HOURS[loc], active,
                           math.exp(rng.gauss(0, 0.6)) * heavy, cc, wfs)  # fmt: skip
                self.members.append(m)
                suffix = pid[len(base) :]
                rows.append({"principal": pid, "name": m.name, "email": f"{first}.{last}{suffix}@acme.example".lower(),
                             "team": team, "department": d.name, "role": role, "title": title, "location": loc,
                             "cost_center": f"CC-{d.code}-{d.teams.index(team) + 1:02d}"})  # fmt: skip
        self.store.upsert_people(self.named + rows)
        self.stats["people"] = len(rows) + len(self.named)

    # ---- daily rollups --------------------------------------------------------------------

    def history(self) -> None:
        today0 = math.floor(self.now / DAY) * DAY
        for d in range(self.days - 1, -1, -1):
            day0 = today0 - d * DAY
            weekend = time.gmtime(day0).tm_wday >= 5 and d > 0
            for m in self.members:
                if not m.active:
                    continue
                if weekend:  # ~12% of a weekday: on-call, deadlines, the odd keen person
                    if self.rng.random() > 0.35:
                        continue
                    factor = 0.45
                else:
                    if self.rng.random() > 0.9:  # leave, sick days, meetings all day
                        continue
                    factor = 1.0
                self.day(m, day0, factor)
            if not weekend:
                self.cloud(day0)
            if len(self.usage) > 50_000 or len(self.events) > 50_000:
                self.flush()
        self.flush()

    def when(self, m: Member, day0: float) -> float:
        lo, hi = m.hours
        return day0 + self.rng.uniform(lo, hi) * 3600

    def day(self, m: Member, day0: float, factor: float) -> None:
        rng, now = self.rng, self.now
        d = m.dept
        common = {"principal": m.id, "team": m.team, "department": d.name}
        checks: dict[tuple[str, str], list] = {}  # (kind, workflow) -> [n, ts, model]
        ci, last = 0.0, 0.0
        for wf, rate in m.workflows.items():
            for _ in range(poisson(rng, rate * m.gateway * factor)):
                t = self.when(m, day0)
                if t >= now:
                    continue
                calls_lo, calls_hi = RUNS[wf][0]
                calls = rng.randint(calls_lo, calls_hi)
                model = rng.choice(RUNS[wf][1])
                pin, pout = PRICES[model]
                inp = int(calls * rng.uniform(*RUNS[wf][2]) * d.ctx)
                out = int(calls * rng.uniform(*RUNS[wf][3]))
                usd = inp * pin + out * pout
                task = f"{PREFIX[wf]}-{rng.randint(100, 9999)}"
                self.usage.append({"ts": t, **common, "workflow": wf, "task": task, "resource": model, "model": model,
                                   "requests": calls, "input_tokens": inp, "output_tokens": out, "usd": usd,
                                   "unit": "token", "source": "gateway", "client": "acme-agent/1.4"})  # fmt: skip
                m.usd_gw += usd
                m.tok_gw += inp + out
                last = max(last, t)
                allowed = calls
                if rng.random() < calls * d.intervene:
                    allowed -= 1
                    decision, sev, finding = FINDINGS[pick(rng, d.findings)]
                    rid = self.id()
                    kind = "check.output" if decision == "warn" else "check.input"
                    self.events.append({"ts": t + 20, "id": rid + ":" + kind[6:9], "source": "gateway", "kind": kind,
                                        **common, "client": "acme-agent/1.4", "task": task, "workflow": wf,
                                        "resource": model, "model": model, "decision": decision, "severity": sev,
                                        "request_id": rid, "detail": {"reason": finding.rsplit(":", 1)[0],
                                                                      "findings": [finding]}})  # fmt: skip
                c = checks.setdefault(("check.input", wf), [0, t, model])
                c[0] += allowed
                c[1] = max(c[1], t)
                tools = int(calls * RUNS[wf][4] * rng.uniform(0.5, 1.5))
                if tools:
                    c = checks.setdefault(("check.tool_call", wf), [0, t, None])
                    c[0] += tools
                    c[1] = max(c[1], t)
                if wf in ("bugfix", "pr_review", "load_test") and rng.random() < 0.6:
                    ci += rng.uniform(4, 25) * (3 if wf == "load_test" else 1)
                if wf == "ui_qa" or (wf == "bugfix" and rng.random() < 0.25) or wf == "load_test":
                    self.lease(m, wf, task, t)
        for (kind, wf), (n, t, model) in checks.items():
            if n > 0:
                self.events.append({"ts": t + 30, "source": "mcp" if kind == "check.tool_call" else "gateway",
                                    "kind": kind, **common, "client": "acme-agent/1.4", "workflow": wf,
                                    "model": model, "decision": "allow", "severity": "info", "n": n})  # fmt: skip
        if ci and last:
            usd = round(ci * 0.008, 6)
            self.usage.append({"ts": last + 600, **common, "resource": "ci_minutes", "quantity": round(ci, 1),
                               "unit": "minute", "usd": usd, "source": "report", "workflow": "bugfix"})  # fmt: skip
            m.usd_gw += usd
        if m.cc_month:
            self.claude_code(m, day0, factor, common)
        m.days += 1

    def claude_code(self, m: Member, day0: float, factor: float, common: dict) -> None:
        """Claude Code sessions, each labelled with a workflow; the day's telemetry rolled up per workflow."""
        rng = self.rng
        sessions = poisson(rng, 2.6 * factor)
        if not sessions:
            return
        daily = m.cc_month / 21 * math.exp(rng.gauss(0, 0.35)) * factor
        if m.dept.code in ("ENG", "PLS"):
            wfs = {"bugfix": 5, "pr_review": 4, "ui_qa": 2 if m.team in m.dept.lease_teams else 0.3}
        else:
            wfs = {"data_analysis": 6, "bugfix": 2, "pr_review": 1}
        who = {**common, "source": "claude_code", "client": "claude-code/2.1.288 (vscode)"}
        per: dict[str, dict[str, Any]] = {}
        for _ in range(sessions):
            t = self.when(m, day0)
            if t >= self.now:
                continue
            usd = daily / sessions * rng.uniform(0.6, 1.4)
            wf = pick(rng, wfs)
            n = max(1, int(usd / 0.3))
            inp, out = int(usd * 0.4 / 3e-6), int(usd * 0.3 / 15e-6)  # the rest is cache reads
            task = f"{PREFIX[wf]}-{rng.randint(100, 9999)}"
            self.usage.append({"ts": t, **who, "workflow": wf, "task": task, "session": self.id(),
                               "resource": "claude_code", "model": "claude-sonnet-4-5", "requests": n,
                               "input_tokens": inp, "output_tokens": out, "usd": usd, "unit": "token"})  # fmt: skip
            a = per.setdefault(wf, dict.fromkeys(("req", "toks", "usd", "t", "acc", "rej", "added", "removed",
                                                  "commits", "prs", "sessions"), 0))  # fmt: skip
            tools = int(n * rng.uniform(0.4, 0.8))
            r = sum(1 for _ in range(tools) if rng.random() < 0.035)
            added = rng.randint(10, 380)
            a["req"] += n
            a["toks"] += inp + out
            a["usd"] += usd
            a["t"] = max(a["t"], t)
            a["acc"] += tools - r
            a["rej"] += r
            a["added"] += added
            a["removed"] += rng.randint(0, added)
            a["commits"] += rng.randint(1, 3) if rng.random() < 0.5 else 0
            a["prs"] += 1 if rng.random() < 0.15 else 0
            a["sessions"] += 1
        for wf, a in per.items():
            m.usd_cc += a["usd"]
            m.tok_cc += a["toks"]
            e = {**who, "ts": a["t"] + 120, "workflow": wf}
            self.events.append({**e, "kind": "cc.api_request", "model": "claude-sonnet-4-5", "decision": "ok",
                                "usd": a["usd"], "tokens": a["toks"], "n": a["req"]})  # fmt: skip
            if a["acc"]:
                self.events.append({**e, "kind": "cc.tool_decision", "decision": "accept", "severity": "info",
                                    "n": a["acc"]})  # fmt: skip
            if a["rej"] == 1:
                self.events.append({**e, "kind": "cc.tool_decision", "tool": rng.choice(["Bash", "Edit", "Write"]),
                                    "decision": "reject", "severity": "low"})  # fmt: skip
            elif a["rej"]:
                self.events.append({**e, "kind": "cc.tool_decision", "decision": "reject", "severity": "low",
                                    "n": a["rej"]})  # fmt: skip
            n = max(2, a["sessions"])  # rollups: n > 1, so they stay out of the activity feed
            for kind, decision, v in (("metric.lines_of_code", "added", a["added"]),
                                      ("metric.lines_of_code", "removed", a["removed"]),
                                      ("metric.session", None, a["sessions"]), ("metric.commit", None, a["commits"]),
                                      ("metric.pull_request", None, a["prs"])):  # fmt: skip
                if v:
                    self.events.append({**e, "kind": kind, "decision": decision, "detail": {"value": v}, "n": n})

    def lease(self, m: Member, wf: str, task: str, t: float) -> None:
        rng = self.rng
        resource, tool, prefix, price = (
            ("simulator", "boot_simulator", "sim", 0.01) if wf == "ui_qa" else ("vm", "create_vm", "vm", 0.05)
        )
        stop = "shutdown_simulator" if resource == "simulator" else "destroy_vm"
        minutes = (
            rng.uniform(8, 35) if wf == "ui_qa" else rng.uniform(60, 120) if wf == "load_test" else rng.uniform(10, 55)
        )
        zombie = rng.random() < 0.04
        if zombie:
            minutes += rng.uniform(40, 120)
        end = t + minutes * 60
        if end >= self.now:
            return
        lid = self.id()[:10]
        usd = round(minutes * price, 6)
        idle = {"simulator": 10, "vm": 30}[resource]
        self.leases.append({"id": lid, "resource": resource, "handle": f"{prefix}-{rng.getrandbits(24):06x}",
                            "server": "demo", "principal": m.id, "team": m.team, "workflow": wf, "task": task,
                            "tool": tool, "started": t, "last_activity": end - (minutes - 6) * 60 if zombie else end,
                            "ended": end, "end_reason": "reclaimed: idle" if zombie else f"stopped by {stop}",
                            "usd": usd, "flags": '["idle"]' if zombie else "[]",
                            "flagged_at": t + (6 + idle) * 60 if zombie else None})  # fmt: skip
        self.usage.append({"ts": end, "principal": m.id, "team": m.team, "department": m.dept.name, "workflow": wf,
                           "task": task, "resource": resource, "quantity": round(minutes, 2), "unit": "minute",
                           "usd": usd, "request_id": lid, "source": "mcp"})  # fmt: skip
        m.usd_gw += usd
        if zombie:
            self.events.append({"ts": t + (6 + idle) * 60, "id": self.id(), "source": "mcp", "kind": "lease.flag",
                                "principal": m.id, "team": m.team, "task": task, "workflow": wf, "resource": resource,
                                "tool": tool, "severity": "low", "request_id": lid,
                                "detail": {"flags": ["idle"], "idle_minutes": idle}})  # fmt: skip

    def cloud(self, day0: float) -> None:
        """A FOCUS-shaped daily cloud charge per engineering and platform team, booked to its manager."""
        if day0 + 23.5 * 3600 >= self.now:
            return
        leads = {}
        for m in self.members:
            if m.dept.code in ("ENG", "PLS") and m.role == "manager":
                leads.setdefault(m.team, m)
        for team, m in sorted(leads.items()):
            usd = round(self.rng.uniform(40, 160), 2)
            self.usage.append({"ts": day0 + 23.5 * 3600, "principal": m.id, "team": team, "department": m.dept.name,
                               "resource": "cloud", "quantity": usd, "unit": "usd", "usd": usd,
                               "source": "billing_export"})  # fmt: skip
            self.events.append({"ts": day0 + 23.5 * 3600, "id": self.id(), "source": "billing_export",
                                "kind": "billing.charge", "principal": m.id, "team": team, "resource": "cloud",
                                "usd": usd, "severity": "info",
                                "detail": {"ServiceName": "Amazon EC2", "SubAccountName": team}})  # fmt: skip

    GROWTH = {"ENG": 0.87, "PLS": 0.93, "DAT": 0.88, "FIN": 1.07, "OPS": 0.95, "SAL": 0.9}  # earlier / now

    def earlier(self) -> None:
        """The window before the seeded one, so `usd_prev`, `adherence_prev` and growth have something to compare:
        per person and day, their usage per workflow and source (one row each, the main resource and model) and
        their checks as rollups. Spend grew by department (finance shrank a little); adherence improved."""
        rng = self.rng
        today0 = math.floor(self.now / DAY) * DAY
        start = today0 - (self.days - 1) * DAY
        members = {m.id: m for m in self.members}
        mix: dict[str, list[dict[str, Any]]] = {}
        for r in self.store._q(
            "SELECT principal, team, department, workflow, source, MAX(resource) resource, MAX(model) model,"
            " MAX(unit) unit, SUM(usd) usd, SUM(input_tokens) i, SUM(output_tokens) o, SUM(requests) requests,"
            " SUM(quantity) quantity FROM usage WHERE metered=1 GROUP BY principal, team, department, workflow, source"
        ):
            mix.setdefault(r["principal"], []).append(r)
        checks = {r["principal"]: r for r in self.store._q(
            "SELECT principal, MAX(workflow) workflow, SUM(COALESCE(n, 1)) n,"
            " SUM(CASE WHEN decision IN ('warn', 'redact', 'block') THEN 1 ELSE 0 END) iv"
            " FROM events WHERE kind LIKE 'check.%' GROUP BY principal")}  # fmt: skip
        present = {pid: max(1, (members[pid].days if pid in members else self.days * 0.75)) for pid in mix}
        for d in range(1, self.days + 1):
            day0 = start - d * DAY
            weekend = time.gmtime(day0).tm_wday >= 5
            for pid, rows in mix.items():
                if rng.random() > (0.35 if weekend else 0.9):
                    continue
                m = members.get(pid)
                code = m.dept.code if m else "ENG"
                f = self.GROWTH.get(code, 0.9) * math.exp(rng.gauss(0, 0.25)) * (0.45 if weekend else 1.0)
                t = self.when(m, day0) if m else day0 + 13 * 3600
                for r in rows:
                    share = f / present[pid]
                    self.usage.append({"ts": t, "principal": pid, "team": r["team"], "department": r["department"],
                                       "workflow": r["workflow"], "resource": r["resource"], "model": r["model"],
                                       "source": r["source"], "unit": r["unit"] or "token",
                                       "requests": round((r["requests"] or 0) * share),
                                       "input_tokens": int((r["i"] or 0) * share),
                                       "output_tokens": int((r["o"] or 0) * share),
                                       "quantity": (r["quantity"] or 0) * share,
                                       "usd": (r["usd"] or 0) * share})  # fmt: skip
                c = checks.get(pid)
                if not c or not c["n"]:
                    continue
                n = max(1, round(c["n"] / present[pid] * f))
                iv = poisson(rng, c["iv"] / present[pid] * f * 1.25)
                who = {"ts": t + 600, "principal": pid, "team": rows[0]["team"], "source": "gateway",
                       "client": "acme-agent/1.4", "kind": "check.input", "workflow": c["workflow"]}  # fmt: skip
                self.events.append({**who, "decision": "allow", "severity": "info", "n": max(2, n - iv)})
                if iv:
                    decision, sev, finding = FINDINGS[pick(rng, m.dept.findings) if m else "email"]
                    self.events.append({**who, "decision": decision, "severity": sev, "n": max(2, iv),
                                        "detail": {"findings": [finding]}})  # fmt: skip
            if len(self.usage) > 50_000 or len(self.events) > 50_000:
                self.flush()
        self.flush()

    def flush(self) -> None:
        if self.usage:
            for r in self.usage:
                r.setdefault("day", day_of(r["ts"]))
            self.store.add_many(self.usage)
        if self.events:
            for e in self.events:
                e.setdefault("id", self.id())
            self.store.add_events_many(self.events)
            self.stats["events"] += len(self.events)
        if self.leases:
            cols = list(self.leases[0])
            with self.store.lock:
                self.store.db.executemany(
                    f"INSERT OR REPLACE INTO leases ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [tuple(x[c] for c in cols) for x in self.leases],
                )
            self.stats["leases"] += len(self.leases)
        self.usage, self.events, self.leases = [], [], []

    # ---- governance across the org --------------------------------------------------------

    def pick_people(self, n: int, depts: tuple[str, ...] = (), cc: bool = False) -> list[Member]:
        pool = [m for m in self.members if m.active and (not depts or m.dept.code in depts) and (m.cc_month or not cc)]
        return self.rng.sample(pool, min(n, len(pool)))

    def incident_workflow(self, rule: str, m: Member) -> str | None:
        """The workflow an incident of this rule would come from, given what the person does."""
        rng, code = self.rng, m.dept.code
        dev = code in ("ENG", "PLS")
        if rule == "unlabeled_resource":
            return None  # leased outside any workflow, by definition
        if rule == "zombie_resource":
            return rng.choice([w for w in ("ui_qa", "bugfix", "load_test") if w in m.workflows] or ["bugfix"])
        if rule in ("rejected_edit_storm", "unapproved_mcp_server", "permission_bypass"):
            return pick(rng, {"bugfix": 3, "pr_review": 2}) if dev else "data_analysis"
        if rule in ("secret_paste", "usage_spike"):
            return pick(rng, {"bugfix": 3, "pr_review": 1, "chat_assist": 1}) if dev else pick(rng, m.workflows)
        if rule == "tool_drift":
            return "data_analysis" if "data_analysis" in m.workflows else "chat_assist"
        if rule in ("probing", "exfiltration"):
            return "chat_assist"
        return pick(rng, {w: r for w, r in m.workflows.items() if w != "unlabeled"} or {"chat_assist": 1})

    def incident(self, ts: float, m: Member, rule: str, detail: str, status: str, policy, by: str = "dana",
                 weight: float | None = None, note: str = "", evidence: list[str] | None = None) -> str:  # fmt: skip
        r = policy.detections.rules[rule]
        w = r.weight if weight is None else weight
        iid = self.id()[:12]
        inc = {"id": iid, "ts": ts, "principal": m.id, "rule": rule, "severity": incident_severity(w), "weight": w,
               "detail": detail, "evidence": evidence or [], "status": "open", "note": "",
               "workflow": self.incident_workflow(rule, m)}  # fmt: skip
        self.store.add_incident(inc)
        if status != "open":
            self.store.set_incident(iid, status, note)
            self.store._x("UPDATE incidents SET updated=? WHERE id=?", (ts + 5400, iid))
            self.store.log_admin(by, f"incident_{status}", iid, note, ts=ts + 5400)
        self.events.append({"ts": ts, "id": "inc-" + iid, "source": "detections", "kind": "incident",
                            "principal": m.id, "team": m.team, "decision": rule, "severity": inc["severity"],
                            "workflow": inc["workflow"],
                            "request_id": (evidence or [None])[-1],
                            "detail": {"incident": iid, "detail": detail, "weight": w,
                                       "evidence": evidence or []}})  # fmt: skip
        return iid

    RULES = [  # rule, weight in the mix, departments (empty: all), detail
        ("secret_paste", 5, ("ENG", "PLS", "DAT"), "repeatedly sends credentials to AI tools"),
        ("new_client", 3, (), "key used from a new client 'python-requests/2.32'"),
        ("usage_spike", 3, ("ENG", "DAT", "PLS"), "{t} tokens in 15 min vs a baseline of {b}"),
        ("zombie_resource", 3, ("ENG",), "vm vm-{h}: idle (52 min idle)"),
        ("unlabeled_resource", 1, ("ENG",), "simulator leased outside any workflow"),
        ("probing", 2, ("SAL", "OPS", "FIN"), "3+ blocked attempts, latest prompt_injection/prompt_injection"),
        ("rejected_edit_storm", 2, ("ENG",), "5+ tool calls rejected in 10 min, latest 'Bash'"),
        ("unapproved_mcp_server", 2, ("ENG", "PLS", "DAT"), "connected to MCP server 'notion-personal'"),
        ("permission_bypass", 1, ("ENG", "PLS"), "switched Claude Code to 'bypassPermissions'"),
        ("tool_drift", 1, ("FIN", "DAT", "SAL"), "first use of sensitive tool 'send_email'"),
    ]
    NOTES = {
        "resolved": ["walked through the secrets guide", "expected: large refactor", "reclaimed automatically",
                     "key rotated", "explained the policy", "fixed the script"],
        "dismissed": ["new laptop, confirmed by phone", "false positive", "old script", "approved tool"],
        "acknowledged": ["follow-up booked", "manager informed", "watching"],
    }  # fmt: skip

    def governance(self, policy, overlay: dict) -> None:
        rng, now = self.rng, self.now
        admins = ("dana", "sam")

        def detail_of(rule: str, text: str) -> str:
            return text.format(
                t=rng.randint(40, 160) * 1000, b=rng.randint(4, 12) * 1000, h=f"{rng.getrandbits(24):06x}"
            )

        # ~150 incidents an admin already handled, spread across the org and the month.
        weights = [w for _, w, _, _ in self.RULES]
        for _ in range(150):
            rule, _, depts, text = rng.choices(self.RULES, weights=weights)[0]
            m = self.pick_people(1, depts)[0]
            ts = now - rng.uniform(0.3, self.days - 0.2) * DAY
            status = rng.choices(["resolved", "dismissed", "acknowledged"], weights=[55, 30, 15])[0]
            if status == "acknowledged" and now - ts > 7 * DAY:
                status = "resolved"
            self.incident(ts, m, rule, detail_of(rule, text), status, policy, rng.choice(admins),
                          note=rng.choice(self.NOTES[status]))  # fmt: skip
        # Open ones nobody has looked at yet: a few days old, and some from the last hours (people at risk).
        for _ in range(4):
            rule, _, depts, text = rng.choices(self.RULES, weights=weights)[0]
            m = self.pick_people(1, depts)[0]
            self.incident(now - rng.uniform(0.3, 5) * DAY, m, rule, detail_of(rule, text), "open", policy)
        for m in self.pick_people(4, ("ENG", "PLS", "DAT"), cc=True):  # alert: score ~35-45
            t = now - rng.uniform(20, 70) * 60
            self.incident(t, m, "unapproved_mcp_server", "connected to MCP server 'notion-personal'", "open", policy)
            self.incident(t + 300, m, "permission_bypass", "switched Claude Code to 'bypassPermissions'", "open",
                          policy)  # fmt: skip
        tightened = self.pick_people(1, ("SAL",))[0]  # tighten: probing then exfiltration, 25 min ago
        t = now - 25 * 60
        tried = []
        for i, reason in enumerate(("prompt_injection/prompt_injection", "prompt_injection/prompt_injection",
                                    "data_exfiltration/upload_external")):  # fmt: skip
            rid = self.id()
            tried.append(rid)
            self.events.append({"ts": t - 300 + i * 90, "id": rid + ":inp", "source": "gateway", "kind": "check.input",
                                "principal": tightened.id, "team": tightened.team, "client": "acme-agent/1.4",
                                "workflow": "chat_assist", "model": "gpt-4o", "decision": "block", "severity": "high",
                                "request_id": rid,
                                "detail": {"reason": reason, "findings": [reason + ":block"]}})  # fmt: skip
        self.incident(t, tightened, "probing", "3+ blocked attempts, latest data_exfiltration/upload_external",
                      "open", policy, evidence=tried)  # fmt: skip
        self.incident(t + 240, tightened, "exfiltration", "data_exfiltration/upload_external (block)", "open", policy,
                      evidence=tried[-1:])  # fmt: skip
        restrict = overlay["principals"]
        reason = "under watch after probing and an exfiltration attempt"
        restrict[tightened.id] = {"budget_scale": 0.25, "reason": reason, "by": "dana", "since": t + 241}
        self.store.log_admin("dana", "tighten", tightened.id, reason, {"budget_scale": 0.25}, ts=t + 241)
        # Restrictions admins set by hand: limited budgets, one revoked key, one restricted contractor.
        for m in self.pick_people(3, ("ENG", "DAT")):
            why = "monthly spend 4x the team median; agreed to cap while reviewing the agent setup"
            ts = now - rng.uniform(1, 12) * DAY
            restrict[m.id] = {"budget_scale": 0.5, "reason": why, "by": "sam", "since": ts}
            self.store.log_admin("sam", "restrict", m.id, why, {"budget_scale": 0.5}, ts=ts)
        m = self.pick_people(1, ("OPS",))[0]
        ts = now - 4 * DAY
        restrict[m.id] = {"status": "revoked", "reason": "left the company", "by": "sam", "since": ts}
        self.store.log_admin("sam", "restrict", m.id, "left the company", {"status": "revoked"}, ts=ts)
        m = self.pick_people(1, ("PLS",), cc=True)[0]
        ts = now - 2 * DAY
        why = "contractor connected an unapproved MCP server to prod tooling"
        self.incident(ts - 600, m, "unapproved_mcp_server", "connected to MCP server 'scraper-mcp'", "acknowledged",
                      policy, note="contract under review")  # fmt: skip
        overlay.setdefault("_risk_holds", {})[m.id] = {"level": "restricted", "reason": why, "at": ts, "by": "dana"}
        self.store.log_admin("dana", "restrict", m.id, why, {"level": "restricted"}, ts=ts)
        self.flush()

    def access(self, policy, overlay: dict) -> None:
        """Grants (about 60, a dozen live) and requests (about 25 pending, 300 decided)."""
        rng, now = self.rng, self.now
        restrict = overlay["principals"]
        kinds = [("prod_db", ("ENG",), "bugfix", 240, "INC-{n}: need to check the {x} table in prod"),
                 ("prod_deploy", ("ENG", "PLS"), None, 60, "release {n} deploy window"),
                 ("external_email", ("SAL", "OPS", "FIN"), None, 480, "send the {x} report to a client")]  # fmt: skip
        tables = ["refunds", "payments", "ledger", "accounts", "cards", "transfers"]
        live_left = 10
        for i in range(56):
            resource, depts, wf, max_min, text = rng.choice(kinds)
            m = self.pick_people(1, depts)[0]
            if m.id in restrict and "status" in restrict[m.id]:
                continue
            live = live_left > 0 and i % 5 == 0
            minutes = rng.choice([30, 60, 120, max_min]) if resource != "prod_deploy" else 60
            minutes = min(minutes, max_min)
            start = now - rng.uniform(0.05, 0.9) * minutes * 60 if live else now - rng.uniform(0.3, self.days) * DAY
            if live:
                live_left -= 1
            reason = text.format(n=rng.randint(100, 999), x=rng.choice(tables))
            by = rng.choice(("dana", "sam"))
            rid = self.store.add_request(m.id, "grant", None, None, reason,
                                         {"resource": resource, "minutes": minutes}, ts=start - 900)  # fmt: skip
            self.store.decide_request(rid, "approved", by, "", ts=start)
            self.store.log_admin(by, "approve_request", m.id, f"request {rid}: {reason}", ts=start)
            r = policy.catalog[resource]
            g = {"id": self.id()[:10], "type": "shield_resource", "resource": resource, "locations": [r.urn],
                 "actions": list(r.actions), "workflow": wf, "expires": start + minutes * 60, "granted_by": by,
                 "reason": f"request {rid}: {reason}", "granted_at": start}  # fmt: skip
            restrict.setdefault(m.id, {}).setdefault("grants", []).append(g)
            self.events.append({"ts": start, "id": self.id(), "source": "admin", "kind": "grant", "principal": m.id,
                                "team": m.team, "resource": resource, "workflow": wf, "severity": "low",
                                "decision": "approved",
                                "detail": {"grant": g["id"], "minutes": minutes, "by": by}})  # fmt: skip
        # Requests: decided over the month, and a queue of pending ones.
        asks = [("quota", "{x} needs more tokens this week", {"scale": 2.0}),
                ("workflow", "{x}: need load tests before the release", {"workflow": "load_test"}),
                ("grant", "INC-{n}: check the {t} table", {"resource": "prod_db", "minutes": 120}),
                ("grant", "send the {t} summary to the auditor",
                 {"resource": "external_email", "minutes": 240})]  # fmt: skip
        topics = ["quarter close", "migration", "onboarding", "audit prep", "incident review", "hackathon"]
        for i in range(300 + 25):
            pending = i >= 300
            kind, text, detail = rng.choice(asks)
            depts = (
                ("ENG", "PLS") if kind == "workflow" else ("ENG", "DAT") if detail.get("resource") == "prod_db" else ()
            )
            m = self.pick_people(1, depts)[0]
            ts = now - (rng.uniform(0.05, 2.5) if pending else rng.uniform(0.5, self.days)) * DAY
            reason = text.format(x=rng.choice(topics), n=rng.randint(100, 999), t=rng.choice(tables))
            d = dict(detail)
            wf = d.pop("workflow", None)
            scale = d.pop("scale", None)
            rid = self.store.add_request(m.id, kind, wf, scale, reason, d or None, ts=ts)
            if not pending:
                status = "approved" if rng.random() < 0.78 else "denied"
                by = rng.choice(("dana", "sam"))
                self.store.decide_request(rid, status, by, "", ts=min(ts + rng.uniform(600, 2 * DAY), now - 60))
                self.store.log_admin(by, "approve_request" if status == "approved" else "deny_request", m.id,
                                     f"request {rid}: {reason}", ts=ts + 3600)  # fmt: skip
        self.flush()

    def running_now(self) -> None:
        """About 40 leases open right now: VMs and simulators in use, and three zombies already flagged
        (reclaimed `reclaim_after_minutes` after the flag, so a minute or two into the demo)."""
        rng, now = self.rng, self.now
        eng = [m for m in self.members if m.active and m.dept.code in ("ENG", "PLS")]
        sims = [m for m in eng if "ui_qa" in m.workflows]
        plan = [("vm", m) for m in rng.sample(eng, min(24, len(eng)))]
        plan += [("simulator", m) for m in rng.sample(sims, min(14, len(sims)))]
        for i, (resource, m) in enumerate(plan):
            zombie = i in (3, 11, 27)
            wf = "ui_qa" if resource == "simulator" else rng.choice(["bugfix", "bugfix", "load_test"])
            if wf == "load_test" and "load_test" not in m.workflows:
                wf = "bugfix"
            start = now - rng.uniform(4, 25 if resource == "simulator" else 50) * 60
            idle = {"simulator": 10, "vm": 30}[resource]
            last = now - (idle + rng.uniform(1, 3)) * 60 if zombie else now - rng.uniform(0, 4) * 60
            start = min(start, last - 60)
            flagged = now - rng.uniform(0.5, 2) * 60 if zombie else None
            tool, prefix = ("boot_simulator", "sim") if resource == "simulator" else ("create_vm", "vm")
            lid = self.id()[:10]
            task = f"{PREFIX[wf]}-{rng.randint(100, 9999)}"
            self.leases.append({"id": lid, "resource": resource, "handle": f"{prefix}-{rng.getrandbits(24):06x}",
                                "server": "demo", "principal": m.id, "team": m.team, "workflow": wf, "task": task,
                                "tool": tool, "started": start, "last_activity": last, "ended": None,
                                "end_reason": None, "usd": 0.0, "flags": '["idle"]' if zombie else "[]",
                                "flagged_at": flagged})  # fmt: skip
            common = {"source": "mcp", "principal": m.id, "team": m.team, "task": task, "workflow": wf,
                      "resource": resource, "tool": tool, "request_id": lid}  # fmt: skip
            self.events.append({**common, "ts": start, "id": self.id(), "kind": "lease.start", "severity": "info"})
            if zombie:
                self.events.append({**common, "ts": flagged, "id": self.id(), "kind": "lease.flag", "severity": "low",
                                    "detail": {"flags": ["idle"], "idle_minutes": idle}})  # fmt: skip
        self.flush()
