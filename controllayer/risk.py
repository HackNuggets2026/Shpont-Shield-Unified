"""Insider risk: a decaying score per person, levels that tighten enforcement, silent alerts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any

import httpx

from .config import Policy
from .state import StateStore
from .types import Context, Principal, Verdict

log = logging.getLogger(__name__)

LEVELS = ("normal", "watch", "restricted")
# Not evidence of intent: rate limits, outages, missing keys.
_IGNORED_CONTROLS = {"budget", "semantic_engine", "auth", "insider_risk"}


class RiskEngine:
    def __init__(self, state: StateStore, base_dir: Path, http: httpx.AsyncClient | None = None):
        self.state = state
        self.base_dir = base_dir
        self.http = http
        self._scores: dict[str, tuple[float, float]] = {}  # principal -> (score, as of)
        self.alerts: deque[dict[str, Any]] = deque(maxlen=500)
        self.sink_errors: deque[str] = deque(maxlen=50)
        self._tasks: set[asyncio.Task] = set()
        self._scored: OrderedDict[tuple[str, str, str], None] = OrderedDict()

    def score(self, policy: Policy, pid: str, now: float | None = None) -> float:
        s, at = self._scores.get(pid, (0.0, 0.0))
        now = now or time.time()
        return s * math.pow(0.5, (now - at) / 3600 / policy.insider_risk.half_life_hours)

    def computed_level(self, policy: Policy, pid: str) -> str:
        s, lv = self.score(policy, pid), policy.insider_risk.levels
        return "restricted" if s >= lv.restricted else "watch" if s >= lv.watch else "normal"

    def level(self, policy: Policy, principal: Principal) -> str:
        """Effective level: the higher of the score-based and the manually set level. An agent is
        at least as restricted as its owner."""
        if not policy.insider_risk.enabled or not principal.authenticated:
            return "normal"
        ids = [principal.id] + ([principal.owner] if principal.owner else [])
        found = [self.computed_level(policy, i) for i in ids]
        found += [self.state.watch[i]["level"] for i in ids if i in self.state.watch]
        return max(found, key=LEVELS.index)

    def reset(self, pid: str) -> None:
        self._scores.pop(pid, None)

    def _add(self, policy: Policy, pid: str, points: float, now: float) -> None:
        self._scores[pid] = (self.score(policy, pid, now) + points, now)

    def observe(self, policy: Policy, ctx: Context, v: Verdict, level_before: str) -> None:
        cfg = policy.insider_risk
        p = ctx.principal
        if not cfg.enabled or not p.authenticated:
            return
        evidence = [f for f in v.findings if f.control not in _IGNORED_CONTROLS]
        if ctx.fetched:
            evidence = []
        key = (p.id, ctx.direction.value, hashlib.sha256(ctx.text.encode()).hexdigest())
        if ctx.resent and key in self._scored:
            evidence = []
        self._scored[key] = None
        self._scored.move_to_end(key)
        if len(self._scored) > 100_000:
            self._scored.popitem(last=False)
        # Shadowed and capped findings still count: they show intent even when not enforced.
        points = sum(
            cfg.weights.get(f.proposed, 0)
            + cfg.category_weights.get(f.category, 0)
            + cfg.category_weights.get(f.control, 0)
            for f in evidence
        )
        now = time.time()
        if points:
            self._add(policy, p.id, points, now)
            if p.owner:
                self._add(policy, p.owner, points * cfg.owner_share, now)
        after = self.level(policy, p)
        reasons = []
        if LEVELS.index(after) > LEVELS.index(level_before):
            reasons.append(f"level {level_before} -> {after}")
        if level_before != "normal" and v.blocked and cfg.alert_on_block_while_watched:
            reasons.append("blocked while under watch")
        hits = sorted({f.category for f in evidence if f.category in cfg.alert_categories})
        if hits:
            reasons.append("alert category: " + ", ".join(hits))
        if reasons:
            self.alert(policy, ctx, v, after, "; ".join(reasons))

    def alert(self, policy: Policy, ctx: Context, v: Verdict, level: str, reason: str) -> dict[str, Any]:
        p = ctx.principal
        event = {
            "ts": time.time(),
            "principal": p.id,
            "owner": p.owner,
            "team": p.team,
            "level": level,
            "score": round(self.score(policy, p.id), 2),
            "reason": reason,
            "request_id": v.request_id,
            "channel": ctx.channel,
            "direction": ctx.direction.value,
            "tool": ctx.tool,
            "action": v.action.value,
            "findings": [f"{f.control}/{f.category}" for f in v.findings],
        }
        self.alerts.append(event)
        for sink in policy.insider_risk.sinks:
            if sink.type == "file" and sink.path:
                path = self.base_dir / sink.path
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a") as fh:
                    fh.write(json.dumps(event) + "\n")
            elif sink.type == "webhook" and sink.url and self.http:
                self._spawn(self._post(sink.url, event))
        return event

    async def _post(self, url: str, event: dict[str, Any]) -> None:
        try:
            r = await self.http.post(url, json=event, timeout=5)  # type: ignore[union-attr]
            r.raise_for_status()
        except httpx.HTTPError as e:
            self.sink_errors.append(f"{url}: {type(e).__name__}: {e}")
            log.warning("alert webhook failed: %s", e)

    def _spawn(self, coro) -> None:
        """Fire and forget: the employee's request never waits on the security webhook."""
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:  # no loop (sync caller): deliver inline
            asyncio.run(coro)
            return
        self._tasks.add(task)  # keep a reference so the task is not garbage-collected mid-flight
        task.add_done_callback(self._tasks.discard)

    def overview(self, policy: Policy) -> list[dict[str, Any]]:
        pids = set(self._scores) | set(self.state.watch)
        rows = []
        for k in policy.identity.api_keys.values():
            if k.principal not in pids:
                continue
            principal = Principal(k.principal, k.team, k.role, kind=k.kind, owner=k.owner)
            rows.append(
                {
                    "principal": k.principal,
                    "team": k.team,
                    "kind": k.kind,
                    "owner": k.owner,
                    "score": round(self.score(policy, k.principal), 2),
                    "computed": self.computed_level(policy, k.principal),
                    "manual": self.state.watch.get(k.principal),
                    "level": self.level(policy, principal),
                }
            )
        return sorted(rows, key=lambda r: -r["score"])
