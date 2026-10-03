"""A deterministic month in a 12-person org: who works how, what it costs, and one insider.

Everyone follows a profile (workflows they run, how much Claude Code they use, working hours). On top of
the routine: simulator and VM leases (a few left running), CI minutes, a cloud bill in FOCUS shape,
approvals and grants, the occasional blocked prompt or redacted email address, low-grade incidents an
admin already closed, and one slow-burn story: frank, an engineer, gets a production-database grant
for a bugfix, starts working late, uses the grant outside the workflow it was given for, and today tries
to send customer data out. Detections have tightened and then quarantined him; the incident is open.
"""

from __future__ import annotations

import math
import random
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from controllayer.config import PolicyStore
from controllayer.controls.budget import BudgetLedger
from controllayer.detections import severity as incident_severity
from controllayer.events import Ingestor
from controllayer.usage import UsageStore

from .org import OrgSeeder

DAY = 86400.0
ADMINS = ("dana", "sam")  # dana: security, sam: platform


@dataclass
class Person:
    id: str
    team: str
    workflows: dict[str, float]  # workflow -> runs per working day
    claude_code: float = 0.0  # Claude Code sessions per working day
    hours: tuple[int, int] = (8, 18)  # UTC working hours
    weekend: float = 0.05  # share of a weekday's work done on a weekend day
    live_demo: bool = False  # used live during the demo: no seeded traffic today, so budgets have room


ORG = [
    Person("alice", "engineering", {"pr_review": 1.5, "bugfix": 1.0, "chat_assist": 2, "ui_qa": 0.5}, 1.5,
           live_demo=True),
    Person("dan", "engineering", {"pr_review": 2.0, "bugfix": 1.5, "chat_assist": 1}, 2.5),
    Person("erin", "engineering", {"ui_qa": 1.5, "bugfix": 0.8, "chat_assist": 1.5}, 1.0, (9, 19)),
    Person("frank", "engineering", {"bugfix": 1.2, "pr_review": 1.0, "chat_assist": 1}, 1.2),
    Person("judy", "engineering", {"bugfix": 1.0, "pr_review": 0.8, "ui_qa": 0.3}, 2.0, (7, 16)),
    Person("bob", "finance", {"data_analysis": 2.0, "chat_assist": 2}, live_demo=True),
    Person("grace", "finance", {"data_analysis": 2.5, "chat_assist": 1}),
    Person("mallory", "finance", {"data_analysis": 1.0, "chat_assist": 3}),
    Person("heidi", "platform", {"bugfix": 0.6, "load_test": 0.3, "chat_assist": 1}, 1.0),
    Person("ops-agent", "platform", {"nightly": 1.0}, hours=(1, 4), weekend=1.0, live_demo=True),
    Person("carol", "interns", {"chat_assist": 3, "ui_qa": 0.4}, hours=(9, 17), live_demo=True),
    Person("ivan", "interns", {"chat_assist": 2.5}, hours=(10, 18)),
]  # fmt: skip

# workflow -> (calls per run, model, input tokens, output tokens, tools)
RUNS: dict[str, tuple[tuple[int, int], str, tuple[int, int], tuple[int, int], list[str]]] = {
    "chat_assist": ((1, 4), "gpt-4o-mini", (300, 1500), (200, 900), []),
    "pr_review": ((2, 6), "gpt-4o", (4000, 15000), (500, 2000), ["read_file", "search_docs", "run_tests"]),
    "bugfix": ((3, 9), "gpt-4o", (3000, 12000), (500, 3000), ["read_file", "search_docs", "run_tests"]),
    "ui_qa": ((2, 5), "gpt-4o-mini", (800, 3000), (200, 800), ["simulator_tap", "simulator_tap"]),
    "data_analysis": ((2, 7), "gpt-4o", (5000, 20000), (800, 2500), ["read_file", "search_docs"]),
    "load_test": ((1, 3), "gpt-4o-mini", (500, 2000), (200, 600), ["run_tests"]),
    "nightly": ((4, 10), "gpt-4o-mini", (1000, 4000), (200, 1000), ["search_docs", "read_file", "run_tests"]),
}
PREFIX = {"pr_review": "PR", "bugfix": "BUG", "ui_qa": "QA", "data_analysis": "FIN", "load_test": "LT",
          "chat_assist": "Q", "nightly": "NIGHTLY"}  # fmt: skip

# Prompts that the gateway blocked or redacted: (decision, severity, finding, workflow)
INTERVENTIONS = [
    ("redact", "low", "pii/email:redact", None),
    ("redact", "low", "pii/phone:redact", None),
    ("block", "high", "secrets/aws_access_key:block", None),
    ("block", "high", "secrets/github_token:block", None),
    ("block", "high", "prompt_injection/prompt_injection:block", None),
    ("log", "info", "off_topic/off_topic:log", "chat_assist"),
    ("warn", "low", "confidential_output/confidential:warn", None),
]


@dataclass
class Seeder:
    store: UsageStore
    ingest: Ingestor
    policies: PolicyStore
    rng: random.Random
    now: float
    days: int
    stats: dict[str, Any] = field(default_factory=lambda: {"events": 0, "leases": 0, "incidents": 0})
    overlay: dict[str, Any] = field(default_factory=lambda: {"principals": {}})

    # ---- helpers --------------------------------------------------------------------

    def ev(self, **e: Any) -> None:
        if e["ts"] >= self.now:
            return
        e.setdefault("source", "gateway")
        e.setdefault("severity", "info")
        self.ingest.ingest(e)
        self.stats["events"] += 1

    def id(self) -> str:
        return uuid.UUID(int=self.rng.getrandbits(128)).hex[:16]

    def when(self, day0: float, p: Person) -> float:
        lo, hi = p.hours
        return day0 + self.rng.uniform(lo, hi) * 3600

    def team_of(self, pid: str) -> str:
        return next(p.team for p in ORG if p.id == pid)

    # ---- routine work ---------------------------------------------------------------

    def run(self, p: Person, wf: str, t: float, task: str, client: str, scale: float = 1.0) -> float:
        """One run of a workflow through the gateway: prompts, tool calls, leases, CI. Returns its end."""
        calls, model, inp, out, tools = RUNS[wf]
        session = self.id()[:8]
        label = None if wf == "nightly" else wf
        for _ in range(self.rng.randint(*calls)):
            rid = self.id()
            t += self.rng.uniform(20, 240)
            base = dict(principal=p.id, team=p.team, client=client, session=session, task=task, workflow=label,
                        model=model, request_id=rid)  # fmt: skip
            if self.rng.random() < (0.06 if p.team == "interns" else 0.025):
                decision, sev, finding, only = self.rng.choice(INTERVENTIONS)
                if only is None or only == wf:
                    metered = decision != "block"
                    self.ev(ts=t, id=rid + ":inp", kind="check.input", decision=decision, severity=sev,
                            detail={"reason": finding.rsplit(":", 1)[0], "findings": [finding]}, **base,
                            **self.tokens(inp, out, scale, metered))  # fmt: skip
                    continue
            self.ev(ts=t, id=rid + ":inp", kind="check.input", decision="allow", **base,
                    **self.tokens(inp, out, scale, True))  # fmt: skip
            for tool in tools:
                if self.rng.random() < 0.5:
                    self.ev(ts=t + 5, id=self.id(), kind="check.tool_call", source="mcp", decision="allow",
                            tool=tool, **{k: v for k, v in base.items() if k != "model"})  # fmt: skip
        if wf in ("ui_qa", "bugfix", "load_test"):
            t = self.leases(p, wf, t, task, session)
        if wf in ("bugfix", "pr_review", "load_test", "nightly") and self.rng.random() < 0.7:
            minutes = round(self.rng.uniform(4, 25) * (3 if wf == "load_test" else 1), 1)
            self.ev(ts=t + 60, id=self.id(), kind="usage.report", source="report", principal=p.id, team=p.team,
                    task=task, workflow=label, resource="ci_minutes", quantity=minutes, unit="minute", meter=True,
                    detail={"quantity": minutes, "unit": "minute"})  # fmt: skip
        return t

    def tokens(self, inp: tuple[int, int], out: tuple[int, int], scale: float, metered: bool) -> dict:
        if not metered:
            return {}
        return {
            "input_tokens": int(self.rng.randint(*inp) * scale),
            "output_tokens": int(self.rng.randint(*out) * scale),
            "meter": True,
        }

    def leases(self, p: Person, wf: str, t: float, task: str, session: str) -> float:
        if wf == "ui_qa":
            plan = [("simulator", "boot_simulator", "sim", self.rng.uniform(8, 35))]
            if self.rng.random() < 0.3:
                plan.append(("simulator", "boot_simulator", "sim", self.rng.uniform(5, 20)))
        elif wf == "load_test":
            plan = [("vm", "create_vm", "vm", self.rng.uniform(60, 120))]
        elif self.rng.random() < 0.45:
            plan = [("vm", "create_vm", "vm", self.rng.uniform(10, 55))]
        else:
            return t
        end = t
        for resource, tool, prefix, minutes in plan:
            zombie = self.rng.random() < 0.04
            if zombie:
                minutes += self.rng.uniform(40, 120)
            end = max(end, self.lease(p, resource, tool, prefix, t, minutes, wf, task, zombie))
        return end

    def lease(self, p: Person, resource: str, tool: str, prefix: str, start: float, minutes: float, wf: str | None,
              task: str | None, zombie: bool = False, open_: bool = False, idle: float = 0) -> float:  # fmt: skip
        handle = f"{prefix}-{self.rng.getrandbits(24):06x}"
        lid = self.id()[:10]
        end = start + minutes * 60
        if end >= self.now and not open_:
            return start
        p_ = self.policies.policy
        lease = {
            "id": lid, "resource": resource, "handle": handle, "server": "demo", "principal": p.id, "team": p.team,
            "workflow": wf, "task": task, "tool": tool, "started": start,
            "last_activity": self.now - idle * 60 if open_ else start + (min(minutes, 6) if zombie else minutes) * 60,
            "ended": None if open_ else end, "end_reason": None if open_ else
            ("reclaimed: idle" if zombie else f"stopped by {p_.catalog[resource].lease.stop_tools[0]}"),
            "usd": 0.0 if open_ else round(minutes * p_.catalog[resource].price.usd_per_unit, 6),
            "flags": ["idle"] if zombie else [],
        }  # fmt: skip
        self.store.save_lease(lease)
        self.stats["leases"] += 1
        common = dict(source="mcp", principal=p.id, team=p.team, task=task, workflow=wf, resource=resource,
                      tool=tool, request_id=lid)  # fmt: skip
        self.ev(ts=start, id=self.id(), kind="lease.start", **common)
        if zombie:
            self.ev(ts=start + 16 * 60, id=self.id(), kind="lease.flag", severity="low",
                    detail={"flags": ["idle"], "idle_minutes": 10}, **common)  # fmt: skip
        if not open_:
            self.ev(ts=end, id=self.id(), kind="lease.stop", quantity=round(minutes, 2), unit="minute", meter=True,
                    detail={"reason": lease["end_reason"], "minutes": round(minutes, 2)}, **common)  # fmt: skip
        return end

    def claude_code(self, p: Person, day0: float, wf_weights: dict[str, float]) -> None:
        start = self.when(day0, p)
        session = str(uuid.UUID(int=self.rng.getrandbits(128)))
        wf = self.rng.choice([w for w in ("bugfix", "pr_review") if w in wf_weights] or ["bugfix"])
        task = f"{PREFIX[wf]}-{self.rng.randint(100, 999)}"
        who = dict(principal=p.id, team=p.team, session=session, workflow=wf, task=task, source="claude_code",
                   client="claude-code/2.1.288 (vscode)")  # fmt: skip
        t = start
        for _ in range(self.rng.randint(4, 14)):
            t += self.rng.uniform(30, 400)
            inp, out = self.rng.randint(800, 9000), self.rng.randint(200, 2500)
            usd = round(inp * 3e-6 + out * 15e-6 + self.rng.uniform(0.01, 0.09), 6)  # incl. cache reads
            self.ev(ts=t, id=self.id(), kind="cc.api_request", model="claude-sonnet-4-5", decision="ok", usd=usd,
                    input_tokens=inp, output_tokens=out, meter=True, **who)  # fmt: skip
            if self.rng.random() < 0.6:
                tool = self.rng.choice(["Edit", "Bash", "Read", "Write", "Grep"])
                rejected = self.rng.random() < 0.08
                self.ev(
                    ts=t + 3,
                    id=self.id(),
                    kind="cc.tool_decision",
                    tool=tool,
                    decision="reject" if rejected else "accept",
                    severity="low" if rejected else "info",
                    **who,
                )
        added = self.rng.randint(10, 380)
        self.ev(ts=t + 30, id=self.id(), kind="metric.lines_of_code", decision="added", detail={"value": added}, **who)
        self.ev(ts=t + 30, id=self.id(), kind="metric.lines_of_code", decision="removed",
                detail={"value": self.rng.randint(0, added)}, **who)  # fmt: skip
        if self.rng.random() < 0.5:
            self.ev(ts=t + 40, id=self.id(), kind="metric.commit", detail={"value": self.rng.randint(1, 3)}, **who)
        if self.rng.random() < 0.15:
            self.ev(ts=t + 50, id=self.id(), kind="metric.pull_request", detail={"value": 1}, **who)
        self.ev(ts=t + 60, id=self.id(), kind="metric.active_time", decision="user",
                detail={"value": round(t - start)}, **who)  # fmt: skip

    def routine(self) -> None:
        today0 = math.floor(self.now / DAY) * DAY
        for d in range(self.days - 1, -1, -1):
            day0 = today0 - d * DAY
            weekend = time.gmtime(day0).tm_wday >= 5 and d > 0  # today is a working day, so the demo has a today
            for p in ORG:
                factor = p.weekend if weekend else 1.0
                if d == 0 and p.live_demo:
                    factor = 0.0  # no seeded traffic today: the live demo starts on a full daily budget at any hour
                client = "acme-agent/1.4" if p.id != "ops-agent" else "ops-runner/2.0"
                for wf, rate in p.workflows.items():
                    for _ in range(self.poisson(rate * factor)):
                        task = f"{PREFIX[wf]}-{self.rng.randint(100, 999)}"
                        self.run(p, wf, self.when(day0, p), task, client)
                for _ in range(self.poisson(p.claude_code * factor)):
                    self.claude_code(p, day0, p.workflows)
            if not weekend:
                self.cloud_bill(day0)

    def poisson(self, lam: float) -> int:
        if lam <= 0:
            return 0
        k, prod, limit = 0, self.rng.random(), math.exp(-lam)
        while prod > limit:
            k += 1
            prod *= self.rng.random()
        return k

    def cloud_bill(self, day0: float) -> None:
        """A FOCUS-shaped daily cloud charge per owning team (what POST /v1/import/focus produces)."""
        for owner, team, lo, hi in (("heidi", "platform", 2.5, 5.5), ("judy", "engineering", 1, 3)):
            usd = round(self.rng.uniform(lo, hi), 2)
            self.ev(ts=day0 + 23.5 * 3600, id=self.id(), kind="billing.charge", source="billing_export",
                    principal=owner, team=team, resource="cloud", usd=usd, quantity=usd, unit="usd", meter=True,
                    detail={"ServiceName": "Amazon EC2", "SubAccountName": team})  # fmt: skip

    # ---- governance history --------------------------------------------------------

    WORKFLOW = {("carol", "secret_paste"): "chat_assist", ("judy", "zombie_resource"): "bugfix",
                ("mallory", "new_client"): "data_analysis", ("ivan", "probing"): "chat_assist",
                ("dan", "usage_spike"): "bugfix", ("grace", "secret_paste"): "data_analysis",
                ("frank", "tool_drift"): "data_analysis", ("frank", "exfiltration"): "data_analysis",
                ("frank", "usage_spike"): "data_analysis", ("frank", "probing"): "data_analysis"}  # fmt: skip

    def incident(self, ts: float, pid: str, rule: str, detail: str, status: str, weight: float | None = None,
                 evidence: list[str] | None = None, note: str = "") -> str:  # fmt: skip
        wf = self.WORKFLOW.get((pid, rule))  # erin's unlabeled simulator has none, by definition
        r = self.policies.policy.detections.rules[rule]
        w = r.weight if weight is None else weight
        inc = {"id": self.id()[:12], "ts": ts, "principal": pid, "rule": rule, "severity": incident_severity(w),
               "weight": w, "detail": detail, "evidence": evidence or [], "status": "open", "note": "",
               "workflow": wf}  # fmt: skip
        self.store.add_incident(inc)
        if status != "open":
            self.store.set_incident(inc["id"], status, note)
        self.store.add_event({"ts": ts, "id": "inc-" + inc["id"], "source": "detections", "kind": "incident",
                              "principal": pid, "team": self.team_of(pid), "decision": rule, "workflow": wf,
                              "severity": inc["severity"], "request_id": (evidence or [None])[-1],
                              "detail": {"incident": inc["id"], "detail": detail, "weight": w,
                                         "evidence": evidence or []}})  # fmt: skip
        self.stats["incidents"] += 1
        return inc["id"]

    def grant(self, pid: str, resource: str, start: float, minutes: float, by: str, reason: str,
              workflow: str | None = None) -> dict:  # fmt: skip
        r = self.policies.policy.catalog[resource]
        g = {"id": self.id()[:10], "type": "shield_resource", "resource": resource, "locations": [r.urn],
             "actions": list(r.actions), "workflow": workflow, "expires": start + minutes * 60, "granted_by": by,
             "reason": reason, "granted_at": start}  # fmt: skip
        self.overlay["principals"].setdefault(pid, {}).setdefault("grants", []).append(g)
        self.store.log_admin(by, "grant", pid, reason, {"resource": resource, "minutes": minutes}, ts=start)
        return g

    def request(self, pid: str, kind: str, ts: float, reason: str, decide: tuple[str, str, float] | None = None,
                **detail: Any) -> str:  # fmt: skip
        wf = detail.pop("workflow", None) if kind == "workflow" else None
        rid = self.store.add_request(pid, kind, wf, detail.pop("scale", None), reason, detail or None, ts=ts)
        if decide:
            status, by, at = decide
            self.store.decide_request(rid, status, by, "", ts=at)
            self.store.log_admin(by, "approve_request" if status == "approved" else "deny_request", pid,
                                 f"request {rid}: {reason}", ts=at)  # fmt: skip
        return rid

    def governance(self) -> None:
        now, D = self.now, DAY
        dana, sam = ADMINS
        # Approvals and grants over the month.
        approved = ("approved", sam, now - 21 * D + 3600)
        self.request("heidi", "workflow", now - 21 * D, "release 2.0 soak tests", approved,
                     workflow="load_test")  # fmt: skip
        self.overlay["principals"].setdefault("heidi", {})["approved_workflows"] = ["load_test"]
        self.request("dan", "grant", now - 12 * D, "INC-288: checkout totals wrong in prod",
                     ("approved", dana, now - 12 * D + 900), resource="prod_db", minutes=120)  # fmt: skip
        self.grant("dan", "prod_db", now - 12 * D + 900, 120, dana, "request: INC-288", workflow="bugfix")
        self.request("ivan", "quota", now - 9 * D, "hackday project", ("denied", sam, now - 9 * D + 7200), scale=3.0)
        self.grant("heidi", "prod_deploy", now - 25 * 60, 60, sam, "release 2.4 deploy window")
        self.request("erin", "grant", now - 3 * 3600, "verify the payment migration on prod data",
                     resource="prod_db", minutes=60)  # fmt: skip
        self.request("judy", "workflow", now - 26 * 3600, "perf regression in search", workflow="load_test")
        self.request("carol", "quota", now - 5 * 3600, "onboarding exercises use more tokens", scale=2.0)
        self.store.log_admin(sam, "edit_workflow", "load_test", "VMs only through the load_test workflow",
                             {"load_test": {"approval": "admin"}}, ts=now - 27 * D)  # fmt: skip

        # Low-grade incidents an admin already handled.
        closed = [
            (24, "carol", "secret_paste", "repeatedly sends credentials to AI tools", "resolved",
             "walked through the secrets guide"),
            (19, "judy", "zombie_resource", "vm vm-3fa1c2: idle (52 min idle)", "resolved", "reclaimed automatically"),
            (15, "mallory", "new_client", "key used from a new client 'python-requests/2.32'", "dismissed",
             "new laptop, confirmed by phone"),
            (11, "ivan", "probing", "3+ blocked attempts, latest prompt_injection/prompt_injection", "resolved",
             "testing jailbreaks from a blog post; explained policy"),
            (8, "erin", "unlabeled_resource", "simulator leased outside any workflow", "dismissed", "old script"),
            (6, "dan", "usage_spike", "61200 tokens in 15 min vs a baseline of 9800", "resolved",
             "large refactor, expected"),
            (4, "grace", "secret_paste", "repeatedly sends credentials to AI tools", "acknowledged",
             "follow-up booked"),
        ]  # fmt: skip
        for days_ago, pid, rule, detail, status, note in closed:
            ts = now - days_ago * D + self.rng.uniform(-3, 3) * 3600
            self.incident(ts, pid, rule, detail, status, note=note)
            self.store.log_admin(dana, f"incident_{status}", pid, note, ts=ts + 5400)

    # ---- the insider -----------------------------------------------------------------

    def frank(self) -> None:
        now, D = self.now, DAY
        frank = next(p for p in ORG if p.id == "frank")
        dana = ADMINS[0]
        base = dict(principal="frank", team="engineering", client="acme-agent/1.4")
        # The bugfix grant, two days ago, scoped to bugfix.
        granted = now - 2 * D - 2 * 3600
        self.request("frank", "grant", granted - 1800, "INC-311: duplicate refunds, need to see the refunds table",
                     ("approved", dana, granted), resource="prod_db", minutes=240)  # fmt: skip
        self.grant("frank", "prod_db", granted, 240, dana, "request: INC-311", workflow="bugfix")
        # Late evenings, growing volume, customer-data reads.
        for d, scale in ((5, 1.4), (4, 1.8), (3, 2.3), (2, 2.8), (1, 3.5)):
            day0 = math.floor((now - d * D) / DAY) * DAY
            t = day0 + self.rng.uniform(20, 22) * 3600
            for _ in range(self.rng.randint(2, 4)):
                wf, task = "data_analysis" if d <= 3 else "chat_assist", f"FIN-{self.rng.randint(100, 999)}"
                t = self.run(frank, wf, t, task, "acme-agent/1.4", scale=scale) + 300
            for _ in range(self.rng.randint(3, 8)):
                t += 90
                self.ev(ts=t, id=self.id(), kind="check.tool_call", source="mcp", decision="allow", tool="read_file",
                        workflow="data_analysis", detail={"findings": ["pii/email:log"]}, **base)  # fmt: skip
        # The grant used in bugfix (allowed), then outside it (refused).
        t = granted + 1200
        for _ in range(14):
            t += self.rng.uniform(60, 400)
            self.ev(ts=t, id=self.id(), kind="check.tool_call", source="mcp", decision="allow", tool="query_prod_db",
                    workflow="bugfix", task="INC-311", resource="prod_db", **base)  # fmt: skip
        t = now - D - 3 * 3600
        refused = []
        for _ in range(3):
            t += 240
            rid = self.id()
            refused.append(rid)
            self.ev(ts=t, id=rid, request_id=rid, kind="check.tool_call", source="mcp", decision="block",
                    severity="medium", tool="query_prod_db", workflow="data_analysis", resource="prod_db",
                    detail={"reason": "access_grant/grant_required: prod_db grant is for workflow 'bugfix'",
                            "findings": ["access_grant/grant_required:block"]}, **base)  # fmt: skip
        drift = self.id()
        self.ev(
            ts=t + 600,
            id=drift,
            request_id=drift,
            kind="check.tool_call",
            source="mcp",
            decision="allow",
            tool="http_get",
            workflow="data_analysis",
            detail={"args": "https://paste.example/upload"},
            **base,
        )
        self.incident(t + 601, "frank", "tool_drift", "first use of sensitive tool 'http_get'", "open",
                      evidence=[drift])  # fmt: skip
        # Today: trying to get customer data out.
        attempts = []
        t = now - 32 * 60
        for text_reason in (
            "data_exfiltration/upload_external: send the customer export to an external URL",
            "data_exfiltration/upload_external: base64 the refunds table into the reply",
            "confidential_output/customer_data: list all customer emails with card numbers",
        ):
            t += self.rng.uniform(90, 240)
            rid = self.id()
            attempts.append(rid)
            self.ev(
                ts=t,
                id=rid,
                request_id=rid,
                kind="check.input",
                decision="block",
                severity="high",
                model="gpt-4o",
                workflow="data_analysis",
                task="FIN-990",
                detail={"reason": text_reason, "findings": [text_reason.split(":")[0] + ":block"]},
                **base,
            )
            # Volume keeps going in between: large prompts that were allowed.
            self.ev(ts=t + 30, id=self.id(), kind="check.input", decision="allow", model="gpt-4o",
                    workflow="data_analysis", task="FIN-990", input_tokens=self.rng.randint(25000, 40000),
                    output_tokens=self.rng.randint(3000, 6000), meter=True, **base)  # fmt: skip
        mail = self.id()
        self.ev(ts=t + 120, id=mail, request_id=mail, kind="check.tool_call", source="mcp", decision="block",
                severity="medium", tool="send_email", workflow="data_analysis", resource="external_email",
                detail={"reason": "access_grant/grant_required: to personal.frank@mail.example",
                        "findings": ["access_grant/grant_required:block"]}, **base)  # fmt: skip
        spike = "142000 tokens in 15 min vs a baseline of 6100"
        t_exfil = now - 24 * 60
        self.incident(t_exfil, "frank", "exfiltration",
                      f"confidential_output/customer_data, data_exfiltration/upload_external (block), with a usage "
                      f"spike: {spike}", "open", weight=80, evidence=attempts[:2])  # fmt: skip
        self.incident(now - 20 * 60, "frank", "usage_spike", spike, "open", evidence=attempts[1:2])
        self.incident(now - 14 * 60, "frank", "probing", "3+ blocked attempts, latest access_grant/grant_required",
                      "open", evidence=[*attempts, mail, *refused])  # fmt: skip
        # Detections responded on their own: tighten, then quarantine.
        auto = "auto:detections"
        self.store.log_admin(auto, "tighten", "frank", "risk score 72.4 reached tighten (60)",
                             {"budget_scale": 0.25}, ts=t_exfil + 1)  # fmt: skip
        self.store.log_admin(auto, "quarantine", "frank", "risk score 118.9 reached quarantine (80)",
                             {"status": "quarantined"}, ts=now - 14 * 60 + 1)  # fmt: skip
        self.overlay["principals"].setdefault("frank", {}).update(
            status="quarantined", budget_scale=0.25, reason="risk score 118.9 reached quarantine (80)", by=auto,
            since=now - 14 * 60 + 1,
        )  # fmt: skip
        self.store.log_admin(dana, "view_events", "frank", "reviewing the exfiltration incident", ts=now - 6 * 60)

    def running_now(self) -> None:
        """Leases still open when the demo starts: an active simulator, and a VM idle for 26 of its 30 allowed
        minutes: the sweep flags it a few minutes into the demo and reclaims it 3 minutes later."""
        alice = next(p for p in ORG if p.id == "alice")
        judy = next(p for p in ORG if p.id == "judy")
        self.lease(alice, "simulator", "boot_simulator", "sim", self.now - 9 * 60, 0, "ui_qa", "QA-512", open_=True)
        self.lease(judy, "vm", "create_vm", "vm", self.now - 41 * 60, 0, "bugfix", "BUG-733", open_=True, idle=26)


NAMES = {
    "alice": ("Alice Moreau", "Senior Software Engineer", "London"),
    "dan": ("Dan Whitaker", "Staff Engineer", "London"),
    "erin": ("Erin Castillo", "Mobile Engineer", "Warsaw"),
    "frank": ("Frank Doyle", "Software Engineer", "London"),
    "judy": ("Judy Tanaka", "Engineering Manager", "Warsaw"),
    "bob": ("Bob Lindqvist", "Financial Analyst", "London"),
    "grace": ("Grace Mbeki", "Senior Financial Analyst", "London"),
    "mallory": ("Mallory Brandt", "Financial Analyst", "Frankfurt"),
    "heidi": ("Heidi Larsen", "Platform Lead", "London"),
    "ops-agent": ("Ops automation agent", "Service account", "London"),
    "carol": ("Carol Okafor", "Intern", "London"),
    "ivan": ("Ivan Petrov", "Intern", "Warsaw"),
}


def named_directory(policies: PolicyStore) -> list[dict]:
    p = policies.policy
    rows = []
    for person in ORG:
        name, title, loc = NAMES[person.id]
        key = p.identity_of(person.id)
        dept = (key.department if key else None) or p.department_of(person.team)
        cc = "".join(w[0] for w in dept.split() if w[0].isalpha())[:3].upper()
        rows.append({"principal": person.id, "name": name, "email": key.email if key else None, "team": person.team,
                     "department": dept, "role": key.role if key else "developer", "title": title, "location": loc,
                     "cost_center": f"CC-{cc}-{person.team}"})  # fmt: skip
    return rows


def seed(
    policy: Path,
    data_dir: Path,
    days: int = 30,
    rng_seed: int = 42,
    now: float | None = None,
    people: int = 5000,
) -> dict:
    """`people` is the size of the org besides the 12 named demo people."""
    data_dir.mkdir(parents=True, exist_ok=True)
    policies = PolicyStore(policy, data_dir=data_dir)
    p = policies.policy
    store = UsageStore(policies.data_path(p.usage.path))
    store.set_org(p.org.name, p.org.team_departments())
    ledger = BudgetLedger()  # in-memory only; the server rebuilds today's counters from the store
    now = float(now or math.floor(time.time()))
    s = Seeder(store, Ingestor(store, ledger, policies), policies, random.Random(rng_seed), now, days)
    store.db.execute("BEGIN")
    try:
        org = OrgSeeder(store, random.Random(rng_seed + 1), now, days, people, named_directory(policies))
        s.routine()
        s.governance()
        s.frank()
        s.running_now()
        org.history()
        org.governance(p, s.overlay)
        org.access(p, s.overlay)
        org.running_now()
        org.earlier()
        store.rebuild_rollups()  # the bulk rows went in without them
        store.db.execute("COMMIT")
    except BaseException:
        store.db.execute("ROLLBACK")
        raise
    policies.write_overlay(s.overlay)
    window = now - days * DAY
    totals = store._q("SELECT COUNT(*) n, COALESCE(SUM(usd), 0) usd FROM usage WHERE metered=1 AND ts>=?", (window,))[0]
    return {
        **s.stats,
        "people": org.stats["people"],
        "events": s.stats["events"] + org.stats["events"],
        "leases": s.stats["leases"] + org.stats["leases"],
        "incidents": len(store.incidents(limit=100_000)),
        "usage_rows": totals["n"],
        "usd": round(totals["usd"], 6),
    }
