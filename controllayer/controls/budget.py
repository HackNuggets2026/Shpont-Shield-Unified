"""Token, cost and rate budgets for commercial APIs and locally hosted models, plus loop detection."""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from ..config import BudgetLimits, Policy
from ..types import Action, Context, Finding


@dataclass
class Usage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    compute_seconds: float = 0.0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def _day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class BudgetLedger:
    def __init__(self) -> None:
        # (scope, key, day) -> Usage; scope is global | team | principal
        self.usage: dict[tuple[str, str, str], Usage] = defaultdict(Usage)
        self.by_model: dict[tuple[str, str], Usage] = defaultdict(Usage)
        self._minute: dict[str, deque[float]] = defaultdict(deque)
        self._calls: dict[str, deque[float]] = defaultdict(deque)

    def _scopes(self, ctx: Context, policy: Policy) -> list[tuple[str, str, BudgetLimits]]:
        b = policy.budgets
        scopes = [("global", "*", b.global_), ("principal", ctx.principal.id, b.per_principal)]
        if ctx.principal.team in b.per_team:
            scopes.append(("team", ctx.principal.team, b.per_team[ctx.principal.team]))
        return scopes

    def pre_check(self, ctx: Context, policy: Policy) -> list[Finding]:
        b = policy.budgets
        if not b.enabled:
            return []
        now, day = time.time(), _day()
        est = estimate_tokens(ctx.text)
        out: list[Finding] = []
        for scope, key, limits in self._scopes(ctx, policy):
            used = self.usage[(scope, key, day)]
            if limits.tokens_per_day is not None and used.tokens + est > limits.tokens_per_day:
                out.append(self._f("token_budget", f"{scope}:{key} {used.tokens}+{est} > {limits.tokens_per_day} tokens/day", b.shadow))
            if limits.usd_per_day is not None and used.usd >= limits.usd_per_day:
                out.append(self._f("cost_budget", f"{scope}:{key} ${used.usd:.4f} >= ${limits.usd_per_day}/day", b.shadow))
            if limits.requests_per_minute is not None:
                q = self._minute[f"{scope}:{key}"]
                while q and now - q[0] > 60:
                    q.popleft()
                if len(q) >= limits.requests_per_minute:
                    out.append(self._f("rate_limit", f"{scope}:{key} > {limits.requests_per_minute} req/min", b.shadow))
        out += self._loop_check(ctx, policy, now)
        if not any(f.action is Action.BLOCK for f in out):
            for scope, key, _ in self._scopes(ctx, policy):
                self._minute[f"{scope}:{key}"].append(now)
        return out

    def _loop_check(self, ctx: Context, policy: Policy, now: float) -> list[Finding]:
        g = policy.budgets.loop_guard
        sig = hashlib.sha256(f"{ctx.principal.id}|{ctx.direction.value}|{ctx.tool}|{ctx.text}".encode()).hexdigest()
        q = self._calls[sig]
        while q and now - q[0] > g.window_seconds:
            q.popleft()
        q.append(now)
        if len(q) > g.max_identical_calls:
            return [self._f("runaway_loop", f"{len(q)} identical calls in {g.window_seconds:.0f}s", policy.budgets.shadow)]
        return []

    def record(
        self,
        ctx: Context,
        policy: Policy,
        model: str,
        input_tokens: int,
        output_tokens: int,
        compute_seconds: float,
    ) -> float:
        price = policy.budgets.pricing.get(model)
        usd = 0.0
        if price:
            usd = (
                input_tokens * price.usd_per_1m_input / 1e6
                + output_tokens * price.usd_per_1m_output / 1e6
                + compute_seconds * price.usd_per_compute_second
            )
        day = _day()
        targets = [self.usage[(scope, key, day)] for scope, key, _ in self._scopes(ctx, policy)]
        targets.append(self.by_model[(model, day)])
        for u in targets:
            u.requests += 1
            u.input_tokens += input_tokens
            u.output_tokens += output_tokens
            u.usd += usd
            u.compute_seconds += compute_seconds
        return usd

    def snapshot(self, policy: Policy) -> dict:
        day = _day()
        b = policy.budgets

        def row(scope: str, key: str, limits: BudgetLimits | None) -> dict:
            u = self.usage.get((scope, key, day), Usage())
            return {
                "scope": scope,
                "key": key,
                "requests": u.requests,
                "tokens": u.tokens,
                "usd": round(u.usd, 6),
                "compute_seconds": round(u.compute_seconds, 6),
                "tokens_limit": limits.tokens_per_day if limits else None,
                "usd_limit": limits.usd_per_day if limits else None,
            }

        rows = [row("global", "*", b.global_)]
        rows += [row("team", t, lim) for t, lim in b.per_team.items()]
        rows += [row("principal", k, b.per_principal) for (s, k, d) in self.usage if s == "principal" and d == day]
        models = [
            {"model": m, "requests": u.requests, "tokens": u.tokens, "usd": round(u.usd, 6), "compute_seconds": round(u.compute_seconds, 6)}
            for (m, d), u in self.by_model.items()
            if d == day
        ]
        return {"day": day, "scopes": rows, "models": models}

    @staticmethod
    def _f(category: str, detail: str, shadow: bool) -> Finding:
        return Finding(
            control="budget",
            category=category,
            action=Action.ALLOW if shadow else Action.BLOCK,
            proposed=Action.BLOCK,
            detail=detail,
            shadow=shadow,
        )
