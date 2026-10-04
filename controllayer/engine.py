"""The control pipeline: gates, budgets, deterministic content checks, then semantic checks."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import unicodedata
from collections import OrderedDict
from typing import Any

import httpx

from . import servertiming
from .audit import AuditLog
from .config import Policy, PolicyStore
from .controls import access, decoys, pii_model, signatures, workflows
from .controls.budget import BudgetLedger
from .controls.patterns import pii, redact, secrets
from .controls.resources import LeaseTracker, check_grants
from .controls.semantic import SemanticGuard
from .decision import DecisionBackend, HeuristicBackend, OllamaSystemOne
from .detections import RiskEngine as DetectionEngine
from .events import Ingestor, verdict_event
from .risk import RiskEngine
from .state import StateStore
from .types import Action, Context, Direction, Finding, Principal, Verdict
from .usage import UsageStore

log = logging.getLogger(__name__)

_STATUS = {"auth": 401, "budget": 429, "resources": 429}  # access_grant: 403
# Model APIs whose reply returns to the caller: OpenAI chat completions and Anthropic messages.
MODEL_CHANNELS = frozenset({"chat", "messages"})


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
        self.state = state or StateStore(store.data_path("data/state.json"))
        self.risk = RiskEngine(self.state, store.base_dir, http)
        self.http = http
        self._pii: pii_model.Detector | None = None
        self._pii_key: tuple | None = None
        self._fixed_backend = backend
        self._backend_key: tuple | None = None
        self._backend: DecisionBackend | None = backend
        p = store.policy
        usage_path = p.usage.path if p.usage.path == ":memory:" else store.data_path(p.usage.path)
        self.usage = UsageStore(usage_path)
        sync_directory(self.usage, p)
        self.ledger = BudgetLedger(self.usage)
        self.leases = LeaseTracker(self.usage, self.ledger)
        # Two risk views: `detections` (behaviour rules over the usage store, incidents, automatic responses)
        # and `risk` (insider-risk levels from findings and external signals).
        self.detections = DetectionEngine(self.usage, store)
        self.leases.on_signal = self.detections.signal
        self.ingestor = Ingestor(self.usage, self.ledger, store)
        self.ingestor.listeners.append(self.detections.observe_event)
        self.leases.urns = {n: r.urn for n, r in p.catalog.items()}
        # (principal, session) -> declared workflow, so later calls in a session inherit its label.
        self.sessions: OrderedDict[tuple[str, str], str] = OrderedDict()
        self.audit = audit or AuditLog(store.data_path(p.audit.path), p.audit.ring_size, p.audit.store_raw_text)
        self.feed = signatures.SignatureFeed(p.signatures.feed)
        self.feed.load(store.base_dir)
        self._team_cache: dict[tuple[str, str], Policy] = {}
        # Verdicts for unmetered history re-checks, so a long conversation costs one model call per message.
        self._seen: OrderedDict[tuple, Verdict] = OrderedDict()
        self._replies: OrderedDict[tuple[str, str], None] = OrderedDict()  # chat replies returned
        store.listeners.append(self._on_reload)

    @property
    def policy(self) -> Policy:
        return self.store.policy

    def _on_reload(self, old: str, new: Policy) -> None:
        sync_directory(self.usage, new)
        self.leases.urns = {n: r.urn for n, r in new.catalog.items()}
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

    def pii_detector(self, policy: Policy) -> pii_model.Detector | None:
        cfg = policy.pii_model
        key = (cfg.backend, cfg.url, cfg.timeout_seconds)
        if key != self._pii_key:
            self._pii, self._pii_key = pii_model.detector_for(policy, self.http), key
        return self._pii

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

    def attribute(self, ctx: Context) -> Context:
        """Fill a session's declared workflow into a call that did not label itself, and remember labels."""
        if ctx.session_id:
            key = (ctx.principal.id, ctx.session_id)
            if ctx.workflow and ctx.workflow_source == "declared":
                self.sessions[key] = ctx.workflow
                self.sessions.move_to_end(key)
                if len(self.sessions) > 50_000:
                    self.sessions.popitem(last=False)
            elif not ctx.workflow and key in self.sessions:
                ctx.workflow, ctx.workflow_source = self.sessions[key], "declared"
        return ctx

    def remember_reply(self, principal: Principal, text: str) -> None:
        key = (principal.id, hashlib.sha256(text.encode()).hexdigest())
        self._replies[key] = None
        self._replies.move_to_end(key)
        if len(self._replies) > 100_000:
            self._replies.popitem(last=False)

    def is_our_reply(self, principal: Principal, text: str) -> bool:
        return (principal.id, hashlib.sha256(text.encode()).hexdigest()) in self._replies

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

        def walk(o: Any, depth: int = 0) -> Any:
            if isinstance(o, dict):
                return {fix(k) if isinstance(k, str) else k: walk(v, depth) for k, v in o.items()}
            if isinstance(o, list):
                return [walk(v, depth) for v in o]
            if not isinstance(o, str):
                return o
            # JSON in a string (an MCP text result, tool-call arguments) is redacted value by value and
            # re-serialised, as flatten() inspects it: a span over the serialised text can eat `", "`.
            if depth < 3 and o.lstrip()[:1] in ("{", "["):
                try:
                    inner = json.loads(o)
                except ValueError:
                    pass
                else:
                    cleaned = walk(inner, depth + 1)
                    return o if cleaned == inner else json.dumps(cleaned, ensure_ascii=False)
            return fix(o)

        out = walk(obj)
        if any(f.action.rank >= Action.REDACT.rank for f in detect(flatten(out))):
            return None
        return out

    async def gate(self, ctx: Context) -> Verdict:
        """Gates and budgets only, for a request whose content is inspected separately."""
        return await self._evaluate(ctx, None, inspect=False)

    async def evaluate(
        self, ctx: Context, extra: dict | None = None, semantic: bool = True, audit_allow: bool = True
    ) -> Verdict:
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
        if cacheable and ctx.resent and key in self._seen:
            self._seen.move_to_end(key)
            servertiming.note_verdict(self._seen[key])
            return self._seen[key]
        verdict = await self._evaluate(ctx, extra, semantic=semantic, audit_allow=audit_allow)
        # Never cached: budget, lease, workflow and outage outcomes (they depend on the moment or on
        # headers, not on the content) and masked verdicts (their placeholders belong to one request's
        # numbering).
        transient = {"budget", "resources", "workflow", "access_grant", "semantic_engine"}
        if (
            cacheable
            and not verdict.masked
            and not any(f.control in transient or f.category == "detector_unavailable" for f in verdict.findings)
        ):
            self._seen[key] = verdict
            if len(self._seen) > 10_000:
                self._seen.popitem(last=False)
        return verdict

    async def _evaluate(
        self, ctx: Context, extra: dict | None, inspect: bool = True, semantic: bool = True, audit_allow: bool = True
    ) -> Verdict:
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
            findings += workflows.check(ctx, policy)
            findings += check_grants(ctx, policy)
        # Traps are noticed whatever else happens to the request, and never change its outcome.
        findings += decoys.check(ctx, policy)
        t = lap("gates", t_start)

        if not _blocked(findings) and ctx.metered and ctx.direction in (Direction.INPUT, Direction.TOOL_CALL):
            findings += self.ledger.pre_check(ctx, policy)
            findings += self.leases.pre_check(ctx, policy)
            t = lap("budget", t)

        if inspect and not _blocked(findings):
            if ctx.channel == "dashboard":
                # The playground shows what each deterministic control cost.
                t0 = t
                findings += secrets.check(ctx, policy.secrets)
                t0 = lap("control:secrets", t0)
                findings += pii.check(ctx, policy.pii)
                t0 = lap("control:pii", t0)
                findings += signatures.check(ctx, policy.signatures, self.feed)
                lap("control:signatures", t0)
            else:
                findings += secrets.check(ctx, policy.secrets)
                findings += pii.check(ctx, policy.pii)
                findings += signatures.check(ctx, policy.signatures, self.feed)
            t = lap("deterministic", t)

        if inspect and not _blocked(findings):
            findings += await pii_model.check(ctx, policy, self.pii_detector(policy))
            t = lap("pii_model", t)
        if ctx.known_pii:
            findings = _drop_known_pii(ctx, findings)

        extra = dict(extra or {})
        if ctx.model:
            extra["provider"] = policy.upstream.backend
        # Deterministic block already decided the outcome; skip the model call.
        if inspect and semantic and not _blocked(findings):
            # Unlabeled chat prompts are attributed to a workflow by the same fast-tier call.
            classify = None
            if ctx.metered and ctx.channel == "chat" and ctx.direction is Direction.INPUT and not ctx.workflow:
                classify = workflows.classifier(policy)
            sem, stats = await SemanticGuard(self.backend(policy)).check(
                ctx, policy, classify, classify_as=workflows.CLASSIFIER
            )
            findings += sem
            if classify is not None:
                ctx.workflow = stats.workflow if stats.workflow not in (None, "other") else workflows.UNLABELED
                ctx.workflow_source = "classified" if ctx.workflow != workflows.UNLABELED else None
            if stats.input_tokens:
                self.ledger.record_guard(ctx, policy.semantic.fast_model, stats.input_tokens)
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
        if inspect:
            semantic_backend = self.backend(policy) if policy.semantic.backend != "off" else None
            findings = await pii_model.apply_overrides(ctx, policy, findings, semantic_backend)
        # Masks can only be put back where a reply returns to the same caller: model-API input.
        reversible = (
            frozenset(policy.pii_model.reversible)
            if ctx.channel in MODEL_CHANNELS and ctx.direction is Direction.INPUT
            else frozenset()
        )
        verdict = _decide(ctx, findings, policy.version, latency, reversible)
        if ctx.workflow:
            extra.setdefault("workflow", ctx.workflow)
        if ctx.task_id:
            extra.setdefault("task", ctx.task_id)
        raw = level != "normal" and policy.insider_risk.watch_capture_raw
        if audit_allow or verdict.action is not Action.ALLOW:
            self.audit.record(ctx, verdict, extra, raw=raw, inspected=inspect)
        if ctx.channel != "dashboard" and (ctx.metered or verdict.action is not Action.ALLOW):
            try:
                self.usage.add_event(verdict_event(ctx, verdict, policy))
            except Exception:  # noqa: BLE001 - the activity stream must never fail the request it records
                log.exception("event write failed")
        self.detections.observe(ctx, verdict, policy)
        if ctx.scored:
            self.risk.observe(policy, ctx, verdict, level)
        servertiming.note_verdict(verdict)
        return verdict


def sync_directory(store: UsageStore, policy: Policy) -> None:
    """The policy's org units into the store, and the people with API keys merged into the directory. A key's
    `department` wins, then the team's department in `org:`; name, title and location stay as the directory has
    them."""
    store.set_org(policy.org.name, policy.org.team_departments())
    rows = []
    for k in policy.identity.api_keys.values():
        if k.kind == "agent":
            continue  # agents act for an owner; the directory lists people
        cur = store.person(k.principal) or {}
        rows.append(
            {
                **cur,
                "principal": k.principal,
                "name": cur.get("name") or k.principal,
                "email": k.email or cur.get("email"),
                "team": k.team,
                "department": k.department or policy.department_of(k.team),
                "role": k.role,
            }
        )
    store.upsert_people(rows)


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


def _drop_known_pii(ctx: Context, findings: list[Finding]) -> list[Finding]:
    out = []
    for f in findings:
        if f.control in pii_model.PII_CONTROLS and f.spans:
            f.spans = [s for s in f.spans if ctx.text[s.start : s.end] not in ctx.known_pii]
            if not f.spans:
                continue
        out.append(f)
    return out


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


def _decide(
    ctx: Context, findings: list[Finding], version: str, latency: dict[str, float], reversible: frozenset[str]
) -> Verdict:
    action = max((f.action for f in findings), key=lambda a: a.rank, default=Action.ALLOW)
    text = ctx.text
    mask_map = ctx.mask_map if ctx.mask_map is not None else {}
    before = dict(mask_map)
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
            text = redact(text, [s for f in redacting for s in f.spans], reversible, mask_map)
        reason = "; ".join(f"{f.control}/{f.category}" for f in redacting)
    elif action is Action.WARN:
        reason = "; ".join(f"{f.control}/{f.category}" for f in findings if f.action is Action.WARN)
    added = {k: v for k, v in mask_map.items() if k not in before}
    masked = any(ph in text for ph in mask_map)
    return Verdict(
        action, text, findings, ctx.request_id, version, latency, status, reason, mask_map=added, masked=masked
    )
