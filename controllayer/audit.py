"""Audit trail (JSONL, exportable) and in-memory metrics for the dashboard."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from collections import Counter, deque
from collections.abc import Callable
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
        self.listeners: list[Callable[[Context, Verdict, dict[str, Any]], None]] = []  # after each decision
        # The whole file, parsed incrementally (see `history`): decisions and notes, oldest first.
        self._hist_events: list[dict[str, Any]] = []
        self._hist_notes: list[dict[str, Any]] = []
        self._hist_offset = 0
        self._preloaded: list[dict[str, Any]] = []  # seed events, which are not in the file
        self._reload_tail()

    def preload(self, events: list[dict[str, Any]]) -> None:
        """Recent events from a seed file: into the ring, and kept for `decisions` since the file lacks them."""
        self.events.extend(events)
        if self.path:
            self._preloaded.extend(events)

    def decisions(self) -> list[dict[str, Any]]:
        """Every decision on record, oldest first: the audit file plus any seeded events."""
        events, _ = self.history()
        if not self._preloaded:
            return events
        return sorted([*self._preloaded, *events], key=lambda e: e.get("ts") or 0)

    def _reload_tail(self) -> None:
        """Refill the event ring from the audit file, so dashboards keep recent history across restarts.
        Counters start again from zero; the file is the complete record. Administrative notes share
        the file and go back to their own ring."""
        if not (self.path and self.path.exists()):
            return
        with self.path.open() as fh:
            for line in deque(fh, maxlen=self.events.maxlen):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue  # a line cut short by a crash
                if "kind" in event and "request_id" not in event:
                    self.notes.append(event)
                else:
                    self.events.append(event)

    def history(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Every decision and administrative note on the trail, oldest first. Reads the audit file (the
        complete record, not just the in-memory ring) and only parses what was appended since the last
        call; without a file, the rings are all there is."""
        if not self.path:
            return list(self.events), list(self.notes)
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size < self._hist_offset:  # rotated or truncated: start over
            self._hist_events, self._hist_notes, self._hist_offset = [], [], 0
        if size > self._hist_offset:
            with self.path.open("rb") as fh:
                fh.seek(self._hist_offset)
                chunk = fh.read(size - self._hist_offset)
            end = chunk.rfind(b"\n") + 1  # leave a half-written last line for next time
            for line in chunk[:end].splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if "kind" in event and "request_id" not in event:
                    self._hist_notes.append(event)
                else:
                    self._hist_events.append(event)
            self._hist_offset += end
        return self._hist_events, self._hist_notes

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
        for listener in self.listeners:
            listener(ctx, v, event)
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


def event_filter(
    action: str | None = None,
    control: str | None = None,
    principal: str | None = None,
    channel: str | None = None,
    direction: str | None = None,
    q: str | None = None,
    since: float | None = None,
    until: float | None = None,
) -> Callable[[dict[str, Any]], bool]:
    """One predicate for the event list, the export and the stats, so they always agree. `action` may be a
    comma-separated list. `principal` matches the actor or, for an agent's events, its owner. `q` is a
    case-insensitive substring of the principal, owner, team, tool, model, reason or a finding's control,
    category or detail. `since` is exclusive (a live cursor), `until` inclusive."""
    actions = {a for a in (action or "").split(",") if a}
    needle = (q or "").lower()

    def matches_text(e: dict[str, Any]) -> bool:
        fields = [e.get(k) for k in ("principal", "owner", "team", "tool", "model", "reason")]
        fields += [f.get(k) for f in e.get("findings") or () for k in ("control", "category", "detail")]
        return any(needle in str(v).lower() for v in fields if v)

    def ok(e: dict[str, Any]) -> bool:
        ts = e.get("ts") or 0
        return (
            (since is None or ts > since)
            and (until is None or ts <= until)
            and (not actions or e.get("action") in actions)
            and (not control or any(f.get("control") == control for f in e.get("findings") or ()))
            and (not principal or principal in (e.get("principal"), e.get("owner")))
            and (not channel or e.get("channel") == channel)
            and (not direction or e.get("direction") == direction)
            and (not needle or matches_text(e))
        )

    return ok


def _pct(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    s = sorted(values)
    k = max(1, math.ceil(p * len(s) / 100))
    return round(s[k - 1], 2)


ACTIONS = ("allow", "log", "warn", "redact", "block")


def stats(events: list[dict[str, Any]], window_s: int, bucket_s: int, now: float | None = None) -> dict[str, Any]:
    """Decision statistics over the last `window_s` seconds of `events` (already filtered): totals by
    action, blocked and redacted rates, top reasons and finding categories, control x action counts,
    latency percentiles per stage and a timeline of counts per bucket per action."""
    now = time.time() if now is None else now
    bucket_s = max(1, bucket_s)
    n_buckets = max(1, min(500, -(-window_s // bucket_s)))
    start = now - n_buckets * bucket_s
    rows = [e for e in events if start < (e.get("ts") or 0) <= now]
    by_action = dict.fromkeys(ACTIONS, 0)
    reasons: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    controls: dict[str, dict[str, int]] = {}
    stages: dict[str, list[float]] = {}
    timeline = [{"t": start + i * bucket_s, **dict.fromkeys(ACTIONS, 0)} for i in range(n_buckets)]
    for e in rows:
        a = e.get("action") or "allow"
        by_action[a] = by_action.get(a, 0) + 1
        if a != "allow" and e.get("reason"):
            reasons[e["reason"]] += 1
        for f in e.get("findings") or ():
            categories[f"{f.get('control')}/{f.get('category')}"] += 1
            c = controls.setdefault(f.get("control") or "?", {})
            c[f.get("action") or "?"] = c.get(f.get("action") or "?", 0) + 1
        for stage, ms in (e.get("latency_ms") or {}).items():
            if isinstance(ms, int | float):
                stages.setdefault(stage, []).append(ms)
        i = min(n_buckets - 1, int(((e.get("ts") or 0) - start) // bucket_s))
        timeline[i][a] = timeline[i].get(a, 0) + 1
    total = len(rows)
    return {
        "window_s": window_s,
        "bucket_s": bucket_s,
        "now": now,
        "total": total,
        "by_action": by_action,
        "blocked_rate": round(by_action.get("block", 0) / total, 4) if total else None,
        "redacted_rate": round(by_action.get("redact", 0) / total, 4) if total else None,
        "top_reasons": [{"reason": k, "count": n} for k, n in reasons.most_common(8)],
        "top_categories": [{"category": k, "count": n} for k, n in categories.most_common(8)],
        "controls": controls,
        "latency_ms": {
            stage: {"p50": _pct(xs, 50), "p95": _pct(xs, 95), "max": round(max(xs), 2), "count": len(xs)}
            for stage, xs in sorted(stages.items())
        },
        "timeline": timeline,
    }


def _stored_text(ctx: Context, v: Verdict) -> str | None:
    """Every detected span masked, even when the action was only log/warn/shadow. Spans index into
    the original text, not the verdict's already redacted one."""
    if v.action is Action.BLOCK:
        return None
    if any(f.action is Action.REDACT and not f.spans for f in v.findings):
        return v.text  # withheld as a whole: nothing to mask precisely
    return redact(ctx.text, [s for f in v.findings for s in f.spans])
