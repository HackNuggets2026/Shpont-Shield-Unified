"""Security detections over usage and verdicts: rules that open incidents.

Each rule is code with its parameters in `policy.yaml` (`detections.rules`). A firing rule opens an
incident with a weight. There is one risk score per person, kept by the insider-risk engine
(`controllayer/risk.py`): a live incident adds its weight to it and decays on that engine's half-life,
so incidents move people along the same ladder (normal, watch, restricted). Rules listed in
`detections.response.restrict_rules` (opening a trap) set the person to restricted at once; only an
admin lifts it.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from fnmatch import fnmatch
from typing import Any

from .config import DetectionRule, Policy, PolicyStore
from .types import Action, Context, Direction, Verdict
from .usage import UsageStore

log = logging.getLogger(__name__)

# Blocks that say nothing about intent: limits, identity, attribution, and secrets (their own rule).
_NOT_PROBING = {
    "budget",
    "resources",
    "auth",
    "model_allowlist",
    "workflow",
    "access_grant",
    "secrets",
    "semantic_engine",
}
_EXFIL_CONTROLS = {"data_exfiltration", "confidential_output"}
_LIVE = ("open", "acknowledged")
LEVELS = ("normal", "watch", "restricted")


def severity(weight: float) -> str:
    return "high" if weight >= 50 else "medium" if weight >= 20 else "low"


class RiskEngine:
    def __init__(self, store: UsageStore, policies: PolicyStore):
        self.store = store
        self.policies = policies
        self.incidents: list[dict[str, Any]] = list(
            reversed(store.incidents(since=time.time() - 7 * 86400, limit=5000))
        )
        self._by_pid: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for i in self.incidents:
            self._by_pid[i["principal"]].append(i)
        self.risk: Any = None  # the insider-risk engine that keeps the one score (set by the layer)
        self._recent: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._last_fired: dict[tuple[str, str], float] = {}
        self._clients: dict[str, set[str]] = defaultdict(set)
        self._tools: dict[str, set[str]] = defaultdict(set)
        self._events: dict[str, int] = defaultdict(int)
        self._tool_calls: dict[str, int] = defaultdict(int)
        self.listeners: list[Any] = []  # callables(incident), e.g. to drop cached admin aggregates
        self._workflow: str | None = None  # of the call or event being observed: the incident's workflow

    # ---- scoring -----------------------------------------------------------------

    def of(self, principal: str) -> list[dict[str, Any]]:
        return self._by_pid.get(principal, [])

    def score(self, principal: str, policy: Policy, now: float | None = None) -> float:
        """The person's one risk score (findings and incidents), as the insider-risk engine keeps it."""
        if self.risk is not None:
            return round(self.risk.score(policy, principal, now), 1)
        now = now or time.time()
        hl = policy.insider_risk.half_life_hours * 3600
        return round(sum(i["weight"] * 0.5 ** ((now - i["ts"]) / hl) for i in self.of(principal) if i["status"] in _LIVE), 1)

    def scores(self, policy: Policy) -> dict[str, float]:
        pids = set(self._by_pid) | (set(self.risk._scores) if self.risk is not None else set())
        return {p: self.score(p, policy) for p in pids}

    def level(self, score: float, policy: Policy, principal: str | None = None) -> str:
        """The level on the insider-risk ladder: given a principal, their effective level (a security
        override or an external signal included); otherwise what the score alone gives."""
        if principal is not None and self.risk is not None:
            return self.risk.own_level(policy, principal)
        lv = policy.insider_risk.levels
        return "restricted" if score >= lv.restricted else "watch" if score >= lv.watch else "normal"

    def set_status(self, iid: str, status: str, note: str = "") -> bool:
        if not self.store.set_incident(iid, status, note):
            return False
        for i in self.incidents:
            if i["id"] == iid:
                i.update(status=status, note=note)
        return True

    # ---- inputs --------------------------------------------------------------------

    def observe(self, ctx: Context, v: Verdict, policy: Policy) -> None:
        d = policy.detections
        if not d.enabled or ctx.channel == "dashboard" or not ctx.principal.authenticated:
            return
        pid, now, rules = ctx.principal.id, time.time(), d.rules
        self._workflow = ctx.workflow
        try:
            self._observe(ctx, v, policy, pid, now, rules)
        except Exception:  # noqa: BLE001 - a detection bug must never fail the request it watches
            log.exception("detection failed")
        finally:
            self._workflow = None
        self._events[pid] += 1

    def _observe(self, ctx: Context, v: Verdict, policy: Policy, pid: str, now: float, rules) -> None:
        for f in v.findings:
            if f.control == "decoys":
                # Opening a trap or moving its content on is deliberate; naming it in a prompt is curiosity.
                rule = "decoy_mention" if f.category.endswith(":mentioned") else "decoy_touch"
                self._fire(rule, pid, f.detail, [v.request_id])

        if ctx.fetched:
            return  # data a tool returned to an agent says nothing about the agent's intent

        if v.blocked:
            blocker = next(f for f in v.findings if f.action is Action.BLOCK)
            if blocker.control not in _NOT_PROBING and self._count(pid, "probing", now, rules):
                self._fire(
                    "probing", pid, f"{rules['probing'].count}+ blocked attempts, latest {v.reason}", [v.request_id]
                )

        if any(f.control == "secrets" for f in v.findings) and ctx.direction in (Direction.INPUT, Direction.TOOL_CALL):
            if self._count(pid, "secret_paste", now, rules):
                self._fire("secret_paste", pid, "repeatedly sends credentials to AI tools", [v.request_id])

        exfil = [
            f
            for f in v.findings
            if (f.control in _EXFIL_CONTROLS or "exfil" in f.category) and f.proposed.rank >= Action.WARN.rank
        ]
        if exfil and "exfiltration" in rules:
            spike = self._spike(pid, rules["exfiltration"], now)
            weight = rules["exfiltration"].weight * (2 if spike else 1)
            what = ", ".join(sorted({f"{f.control}/{f.category}" for f in exfil}))
            detail = f"{what} ({v.action.value})" + (f", with a usage spike: {spike}" if spike else "")
            self._fire("exfiltration", pid, detail, [v.request_id], weight=weight)

        if ctx.metered and ctx.direction is Direction.INPUT and "usage_spike" in rules:
            spike = self._spike(pid, rules["usage_spike"], now)
            if spike:
                self._fire("usage_spike", pid, spike, [v.request_id])

        if ctx.client and "new_client" in rules:
            seen = self._clients[pid]
            if ctx.client not in seen and seen and self._events[pid] >= rules["new_client"].min_history:
                self._fire("new_client", pid, f"key used from a new client {ctx.client!r}", [v.request_id])
            seen.add(ctx.client)

        if ctx.direction is Direction.TOOL_CALL and ctx.tool and ctx.metered:
            r = rules.get("tool_drift")
            seen = self._tools[pid]
            if (
                r
                and ctx.tool not in seen
                and self._tool_calls[pid] >= r.min_history
                and (not r.tools or any(fnmatch(ctx.tool, p) for p in r.tools))
            ):
                self._fire("tool_drift", pid, f"first use of sensitive tool {ctx.tool!r}", [v.request_id])
            seen.add(ctx.tool)
            self._tool_calls[pid] += 1

        r = rules.get("off_hours")
        if r and ctx.metered and ctx.direction is Direction.INPUT:
            hour = time.gmtime(now).tm_hour
            lo, hi = r.hours_utc
            if not lo <= hour < hi:
                self._fire("off_hours", pid, f"activity at {hour:02d}:00 UTC", [v.request_id])

    def observe_event(self, e: dict[str, Any], policy: Policy) -> None:
        """Events from outside the gateway (Claude Code telemetry, CloudEvents)."""
        d = policy.detections
        if not d.enabled or e.get("principal") in (None, "unattributed"):
            return
        self._workflow = e.get("workflow")
        try:
            self._observe_event(e, d.rules, time.time())
        except Exception:  # noqa: BLE001 - a detection bug must never fail ingest
            log.exception("detection failed")
        finally:
            self._workflow = None

    def _observe_event(self, e: dict[str, Any], rules: dict[str, DetectionRule], now: float) -> None:
        pid, kind, ref = e["principal"], e.get("kind") or "", [e.get("id") or ""]
        if "permission" in kind and "mode" in kind and e.get("severity") == "medium":
            self._fire("permission_bypass", pid, f"switched Claude Code to {e.get('decision')!r}", ref)
        if kind == "cc.mcp_server_connection" and e.get("tool") and "unapproved_mcp_server" in rules:
            approved = rules["unapproved_mcp_server"].tools
            if not any(fnmatch(e["tool"], g) for g in approved):
                self._fire("unapproved_mcp_server", pid, f"connected to MCP server {e['tool']!r}", ref)
        if kind == "cc.tool_decision" and e.get("decision") == "reject":
            if self._count(pid, "rejected_edit_storm", now, rules):
                r = rules["rejected_edit_storm"]
                self._fire(
                    "rejected_edit_storm",
                    pid,
                    f"{r.count}+ tool calls rejected in {r.window_minutes:.0f} min, latest {e.get('tool')!r}",
                    ref,
                )

    def signal(self, rule: str, principal: str, detail: str, evidence: list[str], workflow: str | None = None) -> None:
        """Findings from elsewhere (resource leases)."""
        self._workflow = workflow
        try:
            self._fire(rule, principal, detail, evidence)
        finally:
            self._workflow = None

    # ---- helpers -------------------------------------------------------------------

    def _count(self, pid: str, rule: str, now: float, rules: dict[str, DetectionRule]) -> bool:
        r = rules.get(rule)
        if not r or not r.enabled:
            return False
        q = self._recent[(pid, rule)]
        q.append(now)
        while q and now - q[0] > r.window_minutes * 60:
            q.popleft()
        return len(q) >= r.count

    def _spike(self, pid: str, r: DetectionRule, now: float) -> str | None:
        """Tokens in the last window against this principal's average window over the past week."""
        if not r.enabled:
            return None
        window = r.window_minutes * 60
        current = self.store.tokens_between(pid, now - window, now)
        if current < r.min_tokens:
            return None
        start = now - 7 * 86400
        history = self.store.tokens_between(pid, start, now - window)
        windows = max(1.0, (now - window - start) / window)
        baseline = history / windows
        if current < r.factor * baseline:
            return None
        return f"{current} tokens in {r.window_minutes:.0f} min vs a baseline of {baseline:.0f}"

    def _fire(self, rule: str, pid: str, detail: str, evidence: list[str], weight: float | None = None) -> None:
        policy = self.policies.policy
        r = policy.detections.rules.get(rule)
        if not policy.detections.enabled or not r or not r.enabled:
            return
        now = time.time()
        key = (pid, rule)
        if now - self._last_fired.get(key, 0) < r.window_minutes * 60:
            # One incident per rule per window; later evidence joins it.
            open_inc = next((i for i in reversed(self.incidents) if (i["principal"], i["rule"]) == key), None)
            if open_inc is not None:
                open_inc["evidence"] = (open_inc["evidence"] + evidence)[-20:]
            return
        self._last_fired[key] = now
        w = r.weight if weight is None else weight
        inc = {
            "id": uuid.uuid4().hex[:12],
            "ts": now,
            "principal": pid,
            "rule": rule,
            "severity": severity(w),
            "weight": w,
            "detail": detail,
            "evidence": evidence,
            "status": "open",
            "note": "",
            "workflow": self._workflow,
        }
        self.store.add_incident(inc)
        self.incidents.append(inc)
        self._by_pid[pid].append(inc)
        for fn in self.listeners:
            fn(inc)
        self.store.add_event(
            {
                "ts": now,
                "id": "inc-" + inc["id"],
                "source": "detections",
                "kind": "incident",
                "principal": pid,
                "workflow": self._workflow,
                "decision": rule,
                "severity": inc["severity"],
                "request_id": evidence[-1] if evidence else None,
                "detail": {"incident": inc["id"], "detail": detail, "weight": w, "evidence": evidence},
            }
        )
        log.warning("incident %s %s: %s", rule, pid, detail)
        self._respond(pid, policy, now, rule)

    def _respond(self, pid: str, policy: Policy, now: float, rule: str) -> None:
        """The score itself moves the person to watch and restricted. A rule in `restrict_rules`
        (opening a trap) restricts them at once and holds them there until an admin lifts it."""
        resp = policy.detections.response
        if rule not in resp.restrict_rules or self.risk is None:
            return
        reason = "high-severity incident: restricted at once"
        if not resp.auto:
            self.store.log_admin("auto:detections", "recommend_restrict", pid, reason, {"level": "restricted"})
            return
        if self.risk.restrict(pid, reason, "auto:detections"):
            self.store.log_admin("auto:detections", "restrict", pid, reason, {"level": "restricted"})
