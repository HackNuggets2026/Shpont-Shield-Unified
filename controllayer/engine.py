"""The control pipeline: gates, budgets, deterministic content checks, then semantic checks."""

from __future__ import annotations

import json
import time
import unicodedata
from collections import OrderedDict
from typing import Any

import httpx

from .audit import AuditLog
from .config import Policy, PolicyStore
from .controls import access, signatures
from .controls.budget import BudgetLedger
from .controls.patterns import pii, redact, secrets
from .controls.semantic import SemanticGuard
from .decision import DecisionBackend, HeuristicBackend, OllamaSystemOne
from .risk import RiskEngine
from .state import StateStore
from .types import Action, Context, Direction, Finding, Principal, Verdict

_STATUS = {"auth": 401, "budget": 429}


class ControlLayer:
    def __init__(
        self,
        store: PolicyStore,
        backend: DecisionBackend | None = None,
        audit: AuditLog | None = None,
        state: StateStore | None = None,
        http: httpx.AsyncClient | None = None,
    ):
        self.store = store
        self.state = state or StateStore(store.base_dir / "data" / "state.json")
        self.risk = RiskEngine(self.state, store.base_dir, http)
        self._fixed_backend = backend
        self._backend_key: tuple | None = None
        self._backend: DecisionBackend | None = backend
        self.ledger = BudgetLedger()
        p = store.policy
        self.audit = audit or AuditLog(store.base_dir / p.audit.path, p.audit.ring_size, p.audit.store_raw_text)
        self.feed = signatures.SignatureFeed(p.signatures.feed)
        self.feed.load(store.base_dir)
        self._team_cache: dict[tuple[str, str], Policy] = {}
        # Verdicts for unmetered history re-checks, so a long conversation costs one model call per message.
        self._seen: OrderedDict[tuple, Verdict] = OrderedDict()
        store.listeners.append(self._on_reload)

    @property
    def policy(self) -> Policy:
        return self.store.policy

    def _on_reload(self, old: str, new: Policy) -> None:
        self._team_cache.clear()
        self._seen.clear()
        self.audit.store_raw_text = new.audit.store_raw_text
        if new.signatures.feed != self.feed.location:
            feed = signatures.SignatureFeed(new.signatures.feed)
            try:
                feed.load(self.store.base_dir)
            except Exception as e:  # noqa: BLE001 - a bad feed location must never empty the live feed
                self.feed.errors = [f"feed {new.signatures.feed!r} failed to load, keeping {self.feed.location!r}: {e}"]
                return
            self.feed = feed

    def policy_for(self, team: str, watched: bool = False) -> Policy:
        p = self.store.policy
        key = (p.version, team, watched)
        if key not in self._team_cache:
            tp = p.for_team(team)
            self._team_cache[key] = tp.with_overrides(p.insider_risk.watch_controls) if watched else tp
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

    @staticmethod
    def spans_only(v: Verdict) -> bool:
        """True when every redaction in the verdict has spans, i.e. can be cut out precisely."""
        return all(f.spans for f in v.findings if f.action is Action.REDACT)

    def redact_tree(self, obj: Any, principal: Principal, direction: Direction) -> Any | None:
        """Apply span redactions to every key and string in a JSON tree, keeping its shape.

        Returns None when the result still trips a detector (a secret in a number, split across
        fields, or inside escaped JSON), so the caller refuses instead of forwarding it.
        """
        policy = self.policy_for(principal.team)

        def detect(text: str) -> list[Finding]:
            ctx = Context(principal, direction, sanitize(text))
            found = secrets.check(ctx, policy.secrets) + pii.check(ctx, policy.pii)
            return found + signatures.check(ctx, policy.signatures, self.feed)

        def fix(text: str) -> str:
            clean = sanitize(text)
            hits = [f for f in detect(clean) if f.action is Action.REDACT]
            return redact(clean, [sp for f in hits for sp in f.spans]) if hits else text  # untouched unless redacted

        def walk(o: Any) -> Any:
            if isinstance(o, dict):
                return {fix(k) if isinstance(k, str) else k: walk(v) for k, v in o.items()}
            if isinstance(o, list):
                return [walk(v) for v in o]
            return fix(o) if isinstance(o, str) else o

        out = walk(obj)
        if any(f.action.rank >= Action.REDACT.rank for f in detect(flatten(out))):
            return None
        return out

    async def gate(self, ctx: Context) -> Verdict:
        """Gates and budgets only, for a request whose content is inspected separately."""
        return await self._evaluate(ctx, None, inspect=False)

    async def evaluate(self, ctx: Context, extra: dict | None = None, semantic: bool = True) -> Verdict:
        ctx.text = sanitize(ctx.text)
        key = (
            self.policy.version,
            self.feed.loaded_at,
            self.state.version,
            ctx.pii_override,
            self.risk.level(self.policy, ctx.principal),
            semantic,
            ctx.principal.id,
            ctx.direction,
            ctx.model,
            ctx.tool,
            ctx.text,
        )
        # Brokered-resource checks depend on grant expiry (time), so they are never cached.
        cacheable = ctx.resource is None
        if cacheable and not ctx.metered and key in self._seen:
            self._seen.move_to_end(key)
            return self._seen[key]
        verdict = await self._evaluate(ctx, extra, semantic=semantic)
        # Budget and engine-failure outcomes depend on the moment, not the content: never cached.
        if cacheable and not any(f.control in ("budget", "semantic_engine") for f in verdict.findings):
            self._seen[key] = verdict
            if len(self._seen) > 10_000:
                self._seen.popitem(last=False)
        return verdict

    async def _evaluate(self, ctx: Context, extra: dict | None, inspect: bool = True, semantic: bool = True) -> Verdict:
        level = self.risk.level(self.policy, ctx.principal)
        policy = self.policy_for(ctx.principal.team, watched=level != "normal")
        t_start = time.perf_counter()
        latency: dict[str, float] = {}

        def lap(stage: str, t: float) -> float:
            now = time.perf_counter()
            latency[stage] = (now - t) * 1000
            return now

        findings: list[Finding] = access.check_auth(ctx, policy)
        if level == "restricted":
            findings.append(_restricted())
        if not findings:
            findings += access.check_model(ctx, policy) + access.check_resource(ctx, policy, self.state)
            findings += access.check_tool(ctx, policy)
        t = lap("gates", t_start)

        if not _blocked(findings) and ctx.metered and ctx.direction in (Direction.INPUT, Direction.TOOL_CALL):
            findings += self.ledger.pre_check(ctx, policy)
            t = lap("budget", t)

        if inspect and not _blocked(findings):
            findings += secrets.check(ctx, policy.secrets)
            findings += pii.check(ctx, policy.pii)
            findings += signatures.check(ctx, policy.signatures, self.feed)
            t = lap("deterministic", t)

        extra = dict(extra or {})
        # Deterministic block already decided the outcome; skip the model call.
        if inspect and semantic and not _blocked(findings):
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
        raw = level != "normal" and policy.insider_risk.watch_capture_raw
        self.audit.record(ctx, verdict, extra, raw=raw)
        self.risk.observe(policy, ctx, verdict, level)
        return verdict


_INVISIBLE = dict.fromkeys(c for c in range(0x110000) if unicodedata.category(chr(c)) == "Cf")


def sanitize(text: str) -> str:
    """NFKC plus removal of invisible format characters, so `AKIA\u200b...` or full-width
    lookalikes cannot slip past the detectors. The sanitized text is what gets forwarded."""
    return unicodedata.normalize("NFKC", text).translate(_INVISIBLE)


def flatten(obj: Any) -> str:
    """Every key and scalar in a JSON tree, one per line, so detectors see raw text rather than
    JSON escapes (in `"x\\nAKIA..."` the escaped newline glues an `n` to the secret). Strings that
    are themselves JSON (OpenAI tool-call arguments) are decoded and walked too."""
    out: list[str] = []

    def walk(o: Any, depth: int = 0) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                out.append(str(k))
                walk(v, depth)
        elif isinstance(o, list):
            for v in o:
                walk(v, depth)
        elif isinstance(o, str):
            out.append(o)
            if depth < 3 and o.lstrip()[:1] in ("{", "["):
                try:
                    walk(json.loads(o), depth + 1)
                except ValueError:
                    pass
        elif o is not None and not isinstance(o, bool):
            out.append(str(o))

    walk(obj)
    return "\n".join(out)


def _restricted() -> Finding:
    return Finding(
        control="insider_risk",
        category="restricted",
        action=Action.BLOCK,
        proposed=Action.BLOCK,
        detail="access restricted pending security review; contact the security team",
    )


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
