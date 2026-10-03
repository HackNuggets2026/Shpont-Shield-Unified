"""Resource leases: simulators, VMs and browsers an agent holds through MCP tools, metered per minute.

A start tool (boot_simulator, boot-device, create_vm) opens a lease, a stop tool closes it, and activity
tools keep it alive. A lease idle for longer than the resource's `idle_minutes`, or older than its
workflow's `max_minutes`, is flagged as a zombie and, if the resource allows, reclaimed by calling its
stop tool. Closing a lease charges minutes x price against the holder's USD budgets.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable
from fnmatch import fnmatch
from typing import Any

from ..config import Policy, Resource
from ..types import Action, Context, Direction, Finding, Principal
from ..usage import UsageStore
from .budget import BudgetLedger

Signal = Callable[[str, str, str, list[str]], None]  # rule, principal, detail, evidence


def _any(tool: str, patterns: list[str]) -> bool:
    return any(fnmatch(tool, p) for p in patterns)


class LeaseTracker:
    def __init__(self, store: UsageStore, ledger: BudgetLedger):
        self.store = store
        self.ledger = ledger
        self.open: dict[str, dict[str, Any]] = {
            lease["id"]: lease for lease in store.leases(open_only=True, limit=10_000)
        }
        self.on_signal: Signal = lambda *a: None

    def held(self, principal: str, resource: str | None = None) -> list[dict[str, Any]]:
        out = [x for x in self.open.values() if x["principal"] == principal and resource in (None, x["resource"])]
        return sorted(out, key=lambda x: x["started"])

    # ---- before the call: concurrency caps --------------------------------------

    def pre_check(self, ctx: Context, policy: Policy) -> list[Finding]:
        if ctx.direction is not Direction.TOOL_CALL or not ctx.tool or not ctx.metered:
            return []
        out: list[Finding] = []
        wf = policy.menu.workflows.get(ctx.workflow or "") if ctx.workflow_source == "declared" else None
        for name, r in policy.resources.items():
            if not _any(ctx.tool, r.start_tools):
                continue
            if wf is not None and name not in wf.resources:
                out.append(
                    _block("workflow", "resource_not_in_workflow", f"workflow {ctx.workflow!r} does not lease {name}")
                )
                continue
            caps = [r.max_concurrent_per_principal, wf.resources[name].max_concurrent if wf else None]
            caps = [c for c in caps if c is not None]
            held = len(self.held(ctx.principal.id, name))
            if caps and held >= min(caps):
                out.append(
                    _block("resources", "concurrency_limit", f"{ctx.principal.id} already holds {held} {name}(s)")
                )
        return out

    # ---- after a successful call: open, touch or close --------------------------

    def after_call(self, ctx: Context, policy: Policy, server: str, args: dict, result_text: str) -> None:
        tool, now = ctx.tool or "", time.time()
        for name, r in policy.resources.items():
            if _any(tool, r.start_tools):
                self._open(ctx, name, r, server, result_text, now)
            elif _any(tool, r.stop_all_tools):
                for lease in self.held(ctx.principal.id, name):
                    self.close(lease["id"], policy, f"stopped by {tool}", now)
            elif _any(tool, r.stop_tools):
                lease = self._target(ctx.principal.id, name, r, args)
                if lease:
                    self.close(lease["id"], policy, f"stopped by {tool}", now)
            elif _any(tool, r.activity_tools):
                lease = self._target(ctx.principal.id, name, r, args)
                if lease:
                    lease["last_activity"] = now
                    self.store.save_lease(lease)

    def _open(self, ctx: Context, name: str, r: Resource, server: str, result_text: str, now: float) -> None:
        handle = None
        if r.handle:
            m = re.search(r.handle.pattern, result_text)
            handle = m.group(0) if m else None
        lease = {
            "id": uuid.uuid4().hex[:10],
            "resource": name,
            "handle": handle,
            "server": server,
            "principal": ctx.principal.id,
            "team": ctx.principal.team,
            "workflow": ctx.workflow,
            "task": ctx.task_id,
            "tool": ctx.tool,
            "started": now,
            "last_activity": now,
            "ended": None,
            "end_reason": None,
            "usd": 0.0,
            "flags": [],
        }
        self.open[lease["id"]] = lease
        self.store.save_lease(lease)
        if not ctx.workflow or ctx.workflow == "unlabeled":
            self.on_signal(
                "unlabeled_resource", ctx.principal.id, f"{name} leased outside any workflow", [ctx.request_id]
            )

    def _target(self, principal: str, name: str, r: Resource, args: dict) -> dict | None:
        held = self.held(principal, name)
        if r.handle and isinstance(args.get(r.handle.arg), str):
            return next((x for x in held if x["handle"] == args[r.handle.arg]), None)
        return held[-1] if held else None

    def close(self, lease_id: str, policy: Policy, reason: str, now: float | None = None) -> dict | None:
        lease = self.open.pop(lease_id, None)
        if lease is None:
            return None
        now = now or time.time()
        minutes = max(0.0, (now - lease["started"]) / 60)
        r = policy.resources.get(lease["resource"])
        usd = minutes * (r.usd_per_minute if r else 0.0)
        lease.update(ended=now, end_reason=reason, usd=round(usd, 6))
        self.store.save_lease(lease)
        ctx = Context(
            Principal(lease["principal"], lease["team"], ""),
            Direction.TOOL_CALL,
            "",
            workflow=lease["workflow"],
            task_id=lease["task"],
        )
        self.ledger.charge(ctx, lease["resource"], usd, minutes, "minute", ref=lease["id"])
        return lease

    # ---- background: zombies and over-time leases -------------------------------

    def sweep(self, policy: Policy, now: float | None = None) -> list[dict[str, Any]]:
        """Flag zombie and over-time leases; returns the ones to reclaim (still open, caller stops them)."""
        now = now or time.time()
        reclaim = []
        for lease in list(self.open.values()):
            r = policy.resources.get(lease["resource"])
            if r is None:
                continue
            new: list[str] = []
            idle = now - lease["last_activity"]
            if r.idle_minutes is not None and idle > r.idle_minutes * 60 and "idle" not in lease["flags"]:
                new.append("idle")
            wf = policy.menu.workflows.get(lease["workflow"] or "")
            cap = wf.resources.get(lease["resource"]) if wf else None
            if cap and cap.max_minutes and now - lease["started"] > cap.max_minutes * 60:
                if "over_time" not in lease["flags"]:
                    new.append("over_time")
            if not new:
                continue
            lease["flags"] = [*lease["flags"], *new]
            self.store.save_lease(lease)
            detail = (
                f"{lease['resource']} {lease['handle'] or lease['id']}: {', '.join(new)} ({idle / 60:.0f} min idle)"
            )
            self.on_signal("zombie_resource", lease["principal"], detail, [lease["id"]])
            if r.auto_reclaim:
                reclaim.append(lease)
        return reclaim

    def reclaim_request(self, lease: dict, policy: Policy) -> dict | None:
        """The MCP call that stops a lease's resource, or None when there is no way to address it."""
        r = policy.resources.get(lease["resource"])
        if not r or not r.stop_tools or any(c in r.stop_tools[0] for c in "*?["):
            return None
        args = {r.handle.arg: lease["handle"]} if r.handle and lease["handle"] else {}
        return {
            "jsonrpc": "2.0",
            "id": f"reclaim-{lease['id']}",
            "method": "tools/call",
            "params": {"name": r.stop_tools[0], "arguments": args},
        }

    def snapshot(self, policy: Policy, principal: str | None = None, now: float | None = None) -> list[dict]:
        now = now or time.time()
        out = []
        for lease in self.open.values():
            if principal and lease["principal"] != principal:
                continue
            r = policy.resources.get(lease["resource"])
            minutes = (now - lease["started"]) / 60
            out.append(
                {
                    **lease,
                    "minutes": round(minutes, 1),
                    "idle_minutes": round((now - lease["last_activity"]) / 60, 1),
                    "running_usd": round(minutes * (r.usd_per_minute if r else 0.0), 4),
                }
            )
        return sorted(out, key=lambda x: x["started"])


def _block(control: str, category: str, detail: str) -> Finding:
    return Finding(control=control, category=category, action=Action.BLOCK, proposed=Action.BLOCK, detail=detail)
