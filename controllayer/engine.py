"""The control pipeline: gates, budgets, deterministic content checks, then semantic checks."""

from __future__ import annotations

import time

from .audit import AuditLog
from .config import Policy, PolicyStore
from .controls import access, signatures
from .controls.budget import BudgetLedger
from .controls.patterns import pii, redact, secrets
from .controls.semantic import SemanticGuard
from .decision import DecisionBackend, HeuristicBackend, OllamaSystemOne
from .types import Action, Context, Direction, Finding, Verdict

_STATUS = {"auth": 401, "budget": 429}


class ControlLayer:
    def __init__(self, store: PolicyStore, backend: DecisionBackend | None = None, audit: AuditLog | None = None):
        self.store = store
        self._fixed_backend = backend
        self._backend_key: tuple | None = None
        self._backend: DecisionBackend | None = backend
        self.ledger = BudgetLedger()
        p = store.policy
        self.audit = audit or AuditLog(store.base_dir / p.audit.path, p.audit.ring_size, p.audit.store_raw_text)
        self.feed = signatures.SignatureFeed(p.signatures.feed)
        self.feed.load(store.base_dir)
        self._team_cache: dict[tuple[str, str], Policy] = {}
        store.listeners.append(self._on_reload)

    @property
    def policy(self) -> Policy:
        return self.store.policy

    def _on_reload(self, old: str, new: Policy) -> None:
        self._team_cache.clear()
        self.audit.store_raw_text = new.audit.store_raw_text
        if new.signatures.feed != self.feed.location:
            self.feed = signatures.SignatureFeed(new.signatures.feed)
            self.feed.load(self.store.base_dir)

    def policy_for(self, team: str) -> Policy:
        p = self.store.policy
        key = (p.version, team)
        if key not in self._team_cache:
            self._team_cache[key] = p.for_team(team)
        return self._team_cache[key]

    def backend(self, policy: Policy) -> DecisionBackend:
        if self._fixed_backend:
            return self._fixed_backend
        e = policy.semantic
        key = (e.backend, e.url, e.timeout_seconds, e.keep_alive)
        if key != self._backend_key:
            self._backend = (
                OllamaSystemOne(e.url, e.timeout_seconds, e.keep_alive) if e.backend == "ollama" else HeuristicBackend()
            )
            self._backend_key = key
        assert self._backend is not None
        return self._backend

    async def evaluate(self, ctx: Context, extra: dict | None = None) -> Verdict:
        policy = self.policy_for(ctx.principal.team)
        t_start = time.perf_counter()
        latency: dict[str, float] = {}

        def lap(stage: str, t: float) -> float:
            now = time.perf_counter()
            latency[stage] = (now - t) * 1000
            return now

        findings: list[Finding] = access.check_auth(ctx, policy)
        if not findings:
            findings += access.check_model(ctx, policy) + access.check_tool(ctx, policy)
        t = lap("gates", t_start)

        if not _blocked(findings) and ctx.direction in (Direction.INPUT, Direction.TOOL_CALL):
            findings += self.ledger.pre_check(ctx, policy)
            t = lap("budget", t)

        if not _blocked(findings):
            findings += secrets.check(ctx, policy.secrets)
            findings += pii.check(ctx, policy.pii)
            findings += signatures.check(ctx, policy.signatures, self.feed)
            t = lap("deterministic", t)

        extra = dict(extra or {})
        # Deterministic block already decided the outcome; skip the model call.
        if not _blocked(findings):
            sem, stats = await SemanticGuard(self.backend(policy)).check(ctx, policy)
            findings += sem
            latency["semantic"] = (time.perf_counter() - t) * 1000
            if stats.fast_ms:
                latency["semantic_fast"] = stats.fast_ms
            if stats.deep_ms:
                latency["semantic_deep"] = stats.deep_ms
            extra["semantic"] = {
                "escalated": stats.escalated,
                "chunks": stats.chunks,
                "input_tokens": stats.input_tokens,
                "error": stats.error,
            }

        latency["total"] = (time.perf_counter() - t_start) * 1000
        verdict = _decide(ctx, findings, policy.version, latency)
        self.audit.record(ctx, verdict, extra)
        return verdict


def _blocked(findings: list[Finding]) -> bool:
    return any(f.action is Action.BLOCK for f in findings)


def _decide(ctx: Context, findings: list[Finding], version: str, latency: dict[str, float]) -> Verdict:
    action = max((f.action for f in findings), key=lambda a: a.rank, default=Action.ALLOW)
    text = ctx.text
    status, reason = 200, ""
    if action is Action.BLOCK:
        blocker = next(f for f in findings if f.action is Action.BLOCK)
        status = _STATUS.get(blocker.control, 403)
        reason = f"{blocker.control}/{blocker.category}: {blocker.detail}"
        text = ""
    elif action is Action.REDACT:
        redacting = [f for f in findings if f.action is Action.REDACT]
        if any(not f.spans for f in redacting):
            # A semantic verdict has no span to cut, so the whole payload is withheld.
            names = ",".join(sorted({f.control for f in redacting if not f.spans}))
            text = f"[REDACTED:{names}]"
        else:
            text = redact(text, [s for f in redacting for s in f.spans])
        reason = "; ".join(f"{f.control}/{f.category}" for f in redacting)
    elif action is Action.WARN:
        reason = "; ".join(f"{f.control}/{f.category}" for f in findings if f.action is Action.WARN)
    return Verdict(action, text, findings, ctx.request_id, version, latency, status, reason)
