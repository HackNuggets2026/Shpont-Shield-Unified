"""Audit trail (JSONL, exportable) and in-memory metrics for the dashboard."""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from collections import Counter, deque
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .controls.patterns import redact
from .types import Action, Context, Verdict

# Address of the HTTP peer that caused the event, set per request by the gateway.
client_ip: ContextVar[str | None] = ContextVar("client_ip", default=None)


class AuditLog:
    def __init__(self, path: str | Path | None, ring_size: int = 2000, store_raw_text: bool = False):
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.store_raw_text = store_raw_text
        self.events: deque[dict[str, Any]] = deque(maxlen=ring_size)
        self.actions: Counter[str] = Counter()
        self.categories: Counter[str] = Counter()
        self.controls: Counter[str] = Counter()
        self.shadow_hits: Counter[str] = Counter()
        self.by_principal: Counter[str] = Counter()
        self.latency: dict[str, deque[float]] = {}
        self.notes: deque[dict[str, Any]] = deque(maxlen=ring_size)
        self.total = 0

    def record(
        self, ctx: Context, v: Verdict, extra: dict[str, Any] | None = None, raw: bool = False, inspected: bool = True
    ) -> dict[str, Any]:
        """`inspected=False` (gates and budgets only): no detector ran, so nothing could be masked and
        no text is stored."""
        event = {
            "ts": v.ts,
            "request_id": v.request_id,
            "channel": ctx.channel,
            "direction": ctx.direction.value,
            "principal": ctx.principal.id,
            "owner": ctx.principal.owner,
            "team": ctx.principal.team,
            "role": ctx.principal.role,
            "model": ctx.model,
            "tool": ctx.tool,
            "src_ip": client_ip.get(),
            "action": v.action.value,
            "status_code": v.status_code,
            "reason": v.reason,
            "policy_version": v.policy_version,
            "latency_ms": {k: round(x, 2) for k, x in v.latency_ms.items()},
            "text_sha256": hashlib.sha256(ctx.text.encode()).hexdigest(),
            "text": _stored_text(ctx, v) if inspected else None,
            "findings": [
                {
                    "control": f.control,
                    "category": f.category,
                    "action": f.action.value,
                    "proposed": f.proposed.value,
                    "score": round(f.score, 3),
                    "tier": f.tier,
                    "shadow": f.shadow,
                    "detail": f.detail,
                }
                for f in v.findings
            ],
            **(extra or {}),
        }
        if self.store_raw_text or raw:
            event["raw_text"] = ctx.text
        self.total += 1
        self.events.append(event)
        self.actions[v.action.value] += 1
        self.by_principal[f"{ctx.principal.id}:{v.action.value}"] += 1
        for f in v.findings:
            self.controls[f.control] += 1
            self.categories[f"{f.control}/{f.category}"] += 1
            if f.shadow or f.action.rank < f.proposed.rank:
                self.shadow_hits[f"{f.control}/{f.category}:{f.proposed.value}"] += 1
        for stage, ms in v.latency_ms.items():
            self.latency.setdefault(stage, deque(maxlen=1000)).append(ms)
        if self.path:
            with self.path.open("a") as fh:
                fh.write(json.dumps(event) + "\n")
        return event

    def note(self, kind: str, actor: str, **details: Any) -> dict[str, Any]:
        """Administrative events (grants, revocations, watch changes) on the same trail as decisions."""
        event = {"ts": time.time(), "kind": kind, "actor": actor, "src_ip": client_ip.get(), **details}
        self.notes.append(event)
        if self.path:
            with self.path.open("a") as fh:
                fh.write(json.dumps(event) + "\n")
        return event

    def latency_summary(self) -> dict[str, dict[str, float]]:
        out = {}
        for stage, xs in self.latency.items():
            s = sorted(xs)
            out[stage] = {
                "count": len(s),
                "p50": round(statistics.median(s), 2),
                "p95": round(s[min(len(s) - 1, int(len(s) * 0.95))], 2),
                "max": round(s[-1], 2),
            }
        return out


def _stored_text(ctx: Context, v: Verdict) -> str | None:
    """Every detected span masked, even when the action was only log/warn/shadow. Spans index into
    the original text, not the verdict's already redacted one."""
    if v.action is Action.BLOCK:
        return None
    if any(f.action is Action.REDACT and not f.spans for f in v.findings):
        return v.text  # withheld as a whole: nothing to mask precisely
    return redact(ctx.text, [s for f in v.findings for s in f.spans])
