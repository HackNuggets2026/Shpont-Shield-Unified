"""Token, cost and rate budgets for commercial APIs and locally hosted models, plus loop detection."""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from fnmatch import fnmatch

from ..config import BudgetLimits, Policy
from ..types import Action, Context, Finding
from ..usage import UsageStore


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


def _scaled(limits: BudgetLimits, scale: float) -> BudgetLimits:
    if scale == 1.0:
        return limits
    return BudgetLimits(
        requests_per_minute=limits.requests_per_minute,
        tokens_per_day=None if limits.tokens_per_day is None else int(limits.tokens_per_day * scale),
        usd_per_day=None if limits.usd_per_day is None else limits.usd_per_day * scale,
    )


class BudgetLedger:
    def __init__(self, store: UsageStore | None = None) -> None:
        # (scope, key, day) -> Usage; scope is global | team | principal
        self.usage: dict[tuple[str, str, str], Usage] = defaultdict(Usage)
        self.by_model: dict[tuple[str, str], Usage] = defaultdict(Usage)
        self._minute: dict[str, deque[float]] = defaultdict(deque)
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self.store = store
        if store:
            self.rehydrate()

    def rehydrate(self) -> None:
        """Rebuild today's counters from the usage store, so a restart does not reset budgets."""
        assert self.store is not None
        day = _day()
        for r in self.store.day_rows(day):
            self._apply(
                r["principal"],
                r["team"],
                r["model"],
                day,
                r["requests"] or 0,
                r["input_tokens"] or 0,
                r["output_tokens"] or 0,
                r["usd"] or 0.0,
                r["compute_seconds"] or 0.0,
            )

    def _scopes(self, ctx: Context, policy: Policy) -> list[tuple[str, str, BudgetLimits]]:
        b = policy.budgets
        per_principal = _scaled(b.per_principal, policy.budget_scale(ctx.principal.id))
        scopes = [("global", "*", b.global_), ("principal", ctx.principal.id, per_principal)]
        if ctx.principal.team in b.per_team:
            scopes.append(("team", ctx.principal.team, b.per_team[ctx.principal.team]))
        return scopes

    def utilization(self, ctx: Context, policy: Policy) -> float:
        """The fullest of the caller's daily token and USD budgets, 0..1+."""
        day, worst = _day(), 0.0
        for scope, key, limits in self._scopes(ctx, policy):
            used = self.usage.get((scope, key, day), Usage())
            if limits.tokens_per_day:
                worst = max(worst, used.tokens / limits.tokens_per_day)
            if limits.usd_per_day:
                worst = max(worst, used.usd / limits.usd_per_day)
        return worst

    def downgrade(self, ctx: Context, policy: Policy, model: str | None) -> str | None:
        """The cheaper model to use instead of `model`, once the caller is past `downgrade.at`."""
        d = policy.budgets.downgrade
        if not (d.enabled and model and policy.budgets.enabled) or self.utilization(ctx, policy) < d.at:
            return None
        target = next((to for pat, to in d.routes.items() if fnmatch(model, pat)), None)
        if not target or target == model:
            return None
        wf = policy.menu.workflows.get(ctx.workflow or "") if ctx.workflow_source == "declared" else None
        if wf and wf.models and not any(fnmatch(target, pat) for pat in wf.models):
            return None  # the workflow would refuse the cheaper model; let the budget decide instead
        return target

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
                out.append(
                    self._f(
                        "token_budget",
                        f"{scope}:{key} {used.tokens}+{est} > {limits.tokens_per_day} tokens/day",
                        b.shadow,
                    )
                )
            if limits.usd_per_day is not None and used.usd >= limits.usd_per_day:
                out.append(
                    self._f("cost_budget", f"{scope}:{key} ${used.usd:.4f} >= ${limits.usd_per_day}/day", b.shadow)
                )
            if limits.requests_per_minute is not None:
                q = self._minute[f"{scope}:{key}"]
                while q and now - q[0] > 60:
                    q.popleft()
                if len(q) >= limits.requests_per_minute:
                    out.append(self._f("rate_limit", f"{scope}:{key} > {limits.requests_per_minute} req/min", b.shadow))
        out += self._run_check(ctx, policy)
        out += self._loop_check(ctx, policy, now)
        if not any(f.action is Action.BLOCK for f in out):
            for scope, key, _ in self._scopes(ctx, policy):
                self._minute[f"{scope}:{key}"].append(now)
        return out

    def _run_check(self, ctx: Context, policy: Policy) -> list[Finding]:
        """Per-run limit: everything one task of a declared workflow has spent so far."""
        wf = policy.menu.workflows.get(ctx.workflow or "")
        if not (wf and ctx.task_id and self.store and ctx.workflow_source == "declared"):
            return []
        lim = wf.per_run
        if lim.tokens is None and lim.usd is None:
            return []
        used = self.store.run_totals(ctx.principal.id, ctx.workflow or "", ctx.task_id)
        out = []
        run = f"run {ctx.workflow}/{ctx.task_id}"
        if lim.tokens is not None and used["tokens"] + estimate_tokens(ctx.text) > lim.tokens:
            out.append(self._f("run_budget", f"{run}: {used['tokens']} tokens > {lim.tokens}", policy.budgets.shadow))
        if lim.usd is not None and used["usd"] >= lim.usd:
            out.append(self._f("run_budget", f"{run}: ${used['usd']:.4f} >= ${lim.usd}", policy.budgets.shadow))
        return out

    def _loop_check(self, ctx: Context, policy: Policy, now: float) -> list[Finding]:
        g = policy.budgets.loop_guard
        sig = hashlib.sha256(f"{ctx.principal.id}|{ctx.direction.value}|{ctx.tool}|{ctx.text}".encode()).hexdigest()
        if len(self._calls) > 50_000:  # unique prompts would otherwise grow this map forever
            self._calls = defaultdict(
                deque, {k: q for k, q in self._calls.items() if q and now - q[-1] <= g.window_seconds}
            )
        q = self._calls[sig]
        while q and now - q[0] > g.window_seconds:
            q.popleft()
        q.append(now)
        if len(q) > g.max_identical_calls:
            return [
                self._f("runaway_loop", f"{len(q)} identical calls in {g.window_seconds:.0f}s", policy.budgets.shadow)
            ]
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
        pricing = policy.budgets.pricing
        # Exact name first, then glob keys, so a model admitted by an allowlist glob is still priced.
        price = pricing.get(model) or next((p for pat, p in pricing.items() if fnmatch(model, pat)), None)
        usd = 0.0
        if price:
            usd = (
                input_tokens * price.usd_per_1m_input / 1e6
                + output_tokens * price.usd_per_1m_output / 1e6
                + compute_seconds * price.usd_per_compute_second
            )
        self._apply(
            ctx.principal.id, ctx.principal.team, model, _day(), 1, input_tokens, output_tokens, usd, compute_seconds
        )
        if self.store:
            self.store.add(
                **_attribution(ctx),
                resource="llm",
                model=model,
                requests=1,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                usd=usd,
                compute_seconds=compute_seconds,
                request_id=ctx.request_id,
            )
        return usd

    def charge(
        self, ctx: Context, resource: str, usd: float, quantity: float, unit: str, ref: str | None = None
    ) -> None:
        """A non-LLM cost (a closed lease, a reported CI run): counts against the same USD budgets."""
        self._apply(ctx.principal.id, ctx.principal.team, None, _day(), 0, 0, 0, usd, 0.0)
        if self.store:
            self.store.add(
                **_attribution(ctx),
                resource=resource,
                quantity=quantity,
                unit=unit,
                usd=usd,
                request_id=ref or ctx.request_id,
            )

    def record_guard(self, ctx: Context, model: str, input_tokens: int) -> None:
        """The control layer's own decision-model tokens: shown as overhead, never charged to the caller."""
        if self.store and input_tokens:
            self.store.add(
                **_attribution(ctx),
                resource="guard",
                model=model,
                input_tokens=input_tokens,
                request_id=ctx.request_id,
                metered=False,
            )

    def _apply(
        self,
        principal: str,
        team: str,
        model: str | None,
        day: str,
        requests: int,
        input_tokens: int,
        output_tokens: int,
        usd: float,
        compute_seconds: float,
    ) -> None:
        targets = [
            self.usage[("global", "*", day)],
            self.usage[("principal", principal, day)],
            self.usage[("team", team, day)],
        ]
        if model:
            targets.append(self.by_model[(model, day)])
        for u in targets:
            u.requests += requests
            u.input_tokens += input_tokens
            u.output_tokens += output_tokens
            u.usd += usd
            u.compute_seconds += compute_seconds

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
        rows += [
            row("principal", k, _scaled(b.per_principal, policy.budget_scale(k)))
            for (s, k, d) in self.usage
            if s == "principal" and d == day
        ]
        models = [
            {
                "model": m,
                "requests": u.requests,
                "tokens": u.tokens,
                "usd": round(u.usd, 6),
                "compute_seconds": round(u.compute_seconds, 6),
            }
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


def _attribution(ctx: Context) -> dict:
    return {
        "principal": ctx.principal.id,
        "team": ctx.principal.team,
        "workflow": ctx.workflow,
        "task": ctx.task_id,
        "session": ctx.session_id,
    }
