"""Insider risk: a decaying score per person, levels that tighten enforcement, silent alerts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from .config import Policy
from .export import convert
from .state import StateStore
from .types import Context, Principal, Verdict

log = logging.getLogger(__name__)

LEVELS = ("normal", "watch", "restricted")
# Not evidence of intent: rate limits, outages, missing keys.
_IGNORED_CONTROLS = {"budget", "semantic_engine", "auth", "insider_risk"}


def integration_of(source: str) -> str:
    """The integration that wrote a signal source (`<integration>` or `<integration>/<source>`)."""
    return source.split("/", 1)[0]


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
        # Detection incidents (controllayer/detections.py) feed the same score: each live incident adds
        # its rule weight, decaying on this engine's half-life. Set by the layer: pid -> incidents.
        self.incidents_of: Callable[[str], list[dict[str, Any]]] | None = None

    def _reset_at(self, pid: str) -> float:
        return self.state.data.get("risk_reset", {}).get(pid, 0.0)

    def incident_points(self, policy: Policy, pid: str, now: float | None = None) -> float:
        """Live incidents since the last reset, each its weight decayed to `now`."""
        if self.incidents_of is None:
            return 0.0
        now = now or time.time()
        hl = policy.insider_risk.half_life_hours * 3600
        since = self._reset_at(pid)
        return sum(
            i["weight"] * math.pow(0.5, max(0.0, now - i["ts"]) / hl)
            for i in self.incidents_of(pid)
            if i["status"] in ("open", "acknowledged") and i["ts"] > since
        )

    def finding_score(self, policy: Policy, pid: str, now: float | None = None) -> float:
        s, at = self._scores.get(pid, (0.0, 0.0))
        now = now or time.time()
        return s * math.pow(0.5, (now - at) / 3600 / policy.insider_risk.half_life_hours)

    def score(self, policy: Policy, pid: str, now: float | None = None) -> float:
        """Points from findings plus points from detection incidents, both halving every half-life."""
        return self.finding_score(policy, pid, now) + self.incident_points(policy, pid, now)

    def computed_level(self, policy: Policy, pid: str) -> str:
        s, lv = self.score(policy, pid), policy.insider_risk.levels
        return "restricted" if s >= lv.restricted else "watch" if s >= lv.watch else "normal"

    def stored(self, pid: str) -> dict[str, dict[str, Any]]:
        """Unexpired stored signals for a principal, by source, as their integration wrote them."""
        now = time.time()
        return {src: sig for src, sig in self.state.signals.get(pid, {}).items() if sig["expires_at"] > now}

    def signals(self, policy: Policy, pid: str) -> dict[str, dict[str, Any]]:
        """The signals that count, by source: only those of integrations the policy still lists,
        each capped to that integration's current `max_level` and `max_ttl_hours`."""
        now = time.time()
        out = {}
        for src, sig in self.state.signals.get(pid, {}).items():
            cfg = policy.identity.integrations.get(integration_of(src))
            if cfg is None:
                continue
            expires = min(sig["expires_at"], sig["at"] + cfg.max_ttl_hours * 3600)
            if expires > now:
                out[src] = {**sig, "level": min(sig["level"], cfg.max_level, key=LEVELS.index), "expires_at": expires}
        return out

    def put_signal(self, pid: str, source: str, signal: dict[str, Any] | None) -> bool:
        """Store a source's signal, replacing its previous one; None withdraws it. Expired ones are
        dropped on the way. Returns whether the source had an unexpired signal before."""
        active = self.stored(pid)
        had = active.pop(source, None) is not None
        if signal:
            active[source] = signal
        if active:
            self.state.signals[pid] = active
        else:
            self.state.signals.pop(pid, None)
        self.state.save()
        return had

    def own_level(self, policy: Policy, pid: str, manual: bool = True) -> str:
        """A level set by security overrides everything else, in either direction. Without one (auto),
        the score-based level, raised to the strongest active external signal."""
        if manual and pid in self.state.watch:
            return self.state.watch[pid]["level"]
        found = [self.computed_level(policy, pid), *(sig["level"] for sig in self.signals(policy, pid).values())]
        return max(found, key=LEVELS.index)

    def level(self, policy: Policy, principal: Principal, manual: bool = True) -> str:
        """Effective level. An agent is at least as restricted as its owner. `manual=False` gives
        what the principal's own level would be without its override (the owner's still applies)."""
        if not policy.insider_risk.enabled or not principal.authenticated:
            return "normal"
        found = [self.own_level(policy, principal.id, manual)]
        if principal.owner:
            found.append(self.own_level(policy, principal.owner))
        return max(found, key=LEVELS.index)

    def reset(self, pid: str) -> None:
        """Zero the score: findings so far are forgotten and incidents until now stop counting
        (they stay on file for review)."""
        self._scores.pop(pid, None)
        self.state.data.setdefault("risk_reset", {})[pid] = time.time()
        self.state.save()

    def restrict(self, pid: str, reason: str, by: str) -> bool:
        """Hold a principal at restricted (blocks every request) until security sets them back to auto.
        Returns False when they were already held there."""
        if (self.state.watch.get(pid) or {}).get("level") == "restricted":
            return False
        self.state.watch[pid] = {"level": "restricted", "reason": reason, "at": time.time(), "by": by}
        self.state.save()
        return True

    def restore_score(self, pid: str, score: float, at: float) -> None:
        """A score as it stood at `at` (seeded history); it decays from then on."""
        self._scores[pid] = (score, at)

    def _add(self, policy: Policy, pid: str, points: float, now: float) -> None:
        self._scores[pid] = (self.finding_score(policy, pid, now) + points, now)

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
        held = {pid: self.own_level(policy, pid, manual=False) for pid in (p.id, p.owner) if pid in self.state.watch}
        if points:
            self._add(policy, p.id, points, now)
            if p.owner:
                self._add(policy, p.owner, points * cfg.owner_share, now)
        after = self.level(policy, p)
        reasons = []
        if LEVELS.index(after) > LEVELS.index(level_before):
            reasons.append(f"level {level_before} -> {after}")
        if p.id in held and (drift := self.drift(policy, p.id, held[p.id])):
            reasons.append(drift)
        if p.owner in held and (drift := self.drift(policy, p.owner, held[p.owner])):
            owner = self.principal(policy, p.owner)
            self.emit(policy, owner, self.level(policy, owner), f"{drift} (via agent {p.id})", channel=ctx.channel)
        if level_before != "normal" and v.blocked and cfg.alert_on_block_while_watched:
            reasons.append("blocked while under watch")
        hits = sorted({f.category for f in evidence if f.category in cfg.alert_categories})
        if hits:
            reasons.append("alert category: " + ", ".join(hits))
        if reasons:
            self.alert(policy, ctx, v, after, "; ".join(reasons))

    def drift(self, policy: Policy, pid: str, auto_before: str) -> str | None:
        """While security's override holds a level, the auto level can still climb; say so."""
        auto = self.own_level(policy, pid, manual=False)
        if pid in self.state.watch and LEVELS.index(auto) > LEVELS.index(auto_before):
            return f"auto level {auto_before} -> {auto}, held at {self.state.watch[pid]['level']} by security override"
        return None

    @staticmethod
    def principal(policy: Policy, pid: str) -> Principal:
        k = next(k for k in policy.identity.api_keys.values() if k.principal == pid)
        return Principal(k.principal, k.team, k.role, kind=k.kind, owner=k.owner)

    def alert(self, policy: Policy, ctx: Context, v: Verdict, level: str, reason: str) -> dict[str, Any]:
        return self.emit(
            policy,
            ctx.principal,
            level,
            reason,
            request_id=v.request_id,
            channel=ctx.channel,
            direction=ctx.direction.value,
            tool=ctx.tool,
            action=v.action.value,
            findings=[f"{f.control}/{f.category}" for f in v.findings],
        )

    def emit(self, policy: Policy, p: Principal, level: str, reason: str, **context: Any) -> dict[str, Any]:
        """Record a silent alert and send it to every sink."""
        event = {
            "ts": time.time(),
            "principal": p.id,
            "owner": p.owner,
            "team": p.team,
            "level": level,
            "score": round(self.score(policy, p.id), 2),
            "reason": reason,
            "request_id": None,
            "channel": None,
            "direction": None,
            "tool": None,
            "action": None,
            "findings": [],
            **context,
        }
        self.alerts.append(event)
        for sink in policy.insider_risk.sinks:
            doc = convert(event, "alert", sink.format)
            if sink.type == "file" and sink.path:
                path = self.base_dir / sink.path
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a") as fh:
                    fh.write(json.dumps(doc) + "\n")
            elif sink.type == "webhook" and sink.url and self.http:
                self._spawn(self._post(sink.url, doc))
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
        pids = (
            set(self._scores) | set(self.state.watch) | {pid for pid in self.state.signals if self.signals(policy, pid)}
        )
        pids |= {k.principal for k in policy.identity.api_keys.values() if self.incident_points(policy, k.principal)}
        rows = []
        for k in policy.identity.api_keys.values():
            if k.principal not in pids:
                continue
            principal = self.principal(policy, k.principal)
            rows.append(
                {
                    "principal": k.principal,
                    "team": k.team,
                    "kind": k.kind,
                    "owner": k.owner,
                    "score": round(self.score(policy, k.principal), 2),
                    "computed": self.computed_level(policy, k.principal),
                    "manual": self.state.watch.get(k.principal),
                    "signals": [
                        {"source": src, **sig} for src, sig in sorted(self.signals(policy, k.principal).items())
                    ],
                    "auto": self.level(policy, principal, manual=False),
                    "level": self.level(policy, principal),
                }
            )
        return sorted(rows, key=lambda r: -r["score"])
