"""Security detections over usage and verdicts, a decaying risk score per principal, and graduated responses.

Each rule is code with its parameters in `policy.yaml` (`detections.rules`). A firing rule opens an
incident with a weight; a principal's risk score is the sum of their open incidents' weights, halving
every `half_life_minutes`. Crossing `response.tighten` cuts their budgets, crossing `response.quarantine`
limits them to read-only tools. Responses only ever escalate on their own; an admin relaxes them.
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
LEVELS = ("none", "alert", "tighten", "quarantine")


def severity(weight: float) -> str:
    return "high" if weight >= 50 else "medium" if weight >= 20 else "low"


class RiskEngine:
    def __init__(self, store: UsageStore, policies: PolicyStore):
        self.store = store
        self.policies = policies
        self.incidents: list[dict[str, Any]] = list(
            reversed(store.incidents(since=time.time() - 7 * 86400, limit=5000))
        )
        self._recent: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._last_fired: dict[tuple[str, str], float] = {}
        self._clients: dict[str, set[str]] = defaultdict(set)
        self._tools: dict[str, set[str]] = defaultdict(set)
        self._events: dict[str, int] = defaultdict(int)
        self._tool_calls: dict[str, int] = defaultdict(int)
        self.listeners: list[Any] = []  # callables(incident), e.g. to drop cached admin aggregates

    # ---- scoring -----------------------------------------------------------------

    def score(self, principal: str, policy: Policy, now: float | None = None) -> float:
        now = now or time.time()
        hl = policy.detections.half_life_minutes * 60
        return round(
            sum(
                i["weight"] * 0.5 ** ((now - i["ts"]) / hl)
                for i in self.incidents
                if i["principal"] == principal and i["status"] in _LIVE
            ),
            1,
        )

    def scores(self, policy: Policy) -> dict[str, float]:
        return {p: self.score(p, policy) for p in {i["principal"] for i in self.incidents}}

    @staticmethod
    def level(score: float, policy: Policy) -> str:
        r = policy.detections.response
        return (
            "quarantine"
            if score >= r.quarantine
            else "tighten"
            if score >= r.tighten
            else ("alert" if score >= r.alert else "none")
        )

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
        try:
            self._observe(ctx, v, policy, pid, now, rules)
        except Exception:  # noqa: BLE001 - a detection bug must never fail the request it watches
            log.exception("detection failed")
        self._events[pid] += 1

    def _observe(self, ctx: Context, v: Verdict, policy: Policy, pid: str, now: float, rules) -> None:
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
        try:
            self._observe_event(e, d.rules, time.time())
        except Exception:  # noqa: BLE001 - a detection bug must never fail ingest
            log.exception("detection failed")

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

    def signal(self, rule: str, principal: str, detail: str, evidence: list[str]) -> None:
        """Findings from elsewhere (resource leases)."""
        self._fire(rule, principal, detail, evidence)

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
        }
        self.store.add_incident(inc)
        self.incidents.append(inc)
        for fn in self.listeners:
            fn(inc)
        self.store.add_event(
            {
                "ts": now,
                "id": "inc-" + inc["id"],
                "source": "detections",
                "kind": "incident",
                "principal": pid,
                "decision": rule,
                "severity": inc["severity"],
                "request_id": evidence[-1] if evidence else None,
                "detail": {"incident": inc["id"], "detail": detail, "weight": w, "evidence": evidence},
            }
        )
        log.warning("incident %s %s: %s", rule, pid, detail)
        self._respond(pid, policy, now)

    def _respond(self, pid: str, policy: Policy, now: float) -> None:
        resp = policy.detections.response
        score = self.score(pid, policy, now)
        level = self.level(score, policy)
        pp = policy.principal(pid)
        patch: dict[str, Any] | None = None
        if level == "quarantine" and pp.status == "active":
            patch = {"status": "quarantined"}
        elif level == "tighten" and pp.status == "active" and pp.budget_scale > resp.tighten_budget_scale:
            patch = {"budget_scale": resp.tighten_budget_scale}
        if patch is None:
            return
        reason = f"risk score {score} reached {level} ({getattr(resp, level):.0f})"
        if not resp.auto:
            self.store.log_admin("auto:detections", f"recommend_{level}", pid, reason, patch)
            return
        patch |= {"reason": reason, "by": "auto:detections", "since": now}
        try:
            self.policies.write_overlay({"principals": {pid: patch}})
        except ValueError as e:
            log.error("automatic %s of %s failed: %s", level, pid, e)
            return
        self.store.log_admin("auto:detections", level, pid, reason, patch)
