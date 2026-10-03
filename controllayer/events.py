"""One event model for everything the control plane observes, and one way in: `Ingestor.ingest`.

An event is a flat record (the `events` table): when, where from (`source`), what (`kind`), who (principal,
team, client, session), what for (workflow, task), on what (resource + URN, model, tool), the decision, and
what it cost. Field names follow OpenTelemetry GenAI conventions and FOCUS where they overlap; external
producers send CloudEvents 1.0 (`from_cloudevent`).

Events that carry cost or tokens and are marked `meter` also go to the `usage` money ledger and count
against today's budgets; the gateway meters its own calls directly and only mirrors verdicts here.
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any

from .config import Policy, PolicyStore
from .controls.budget import BudgetLedger
from .controls.resources import resolve
from .types import Action, Context, Verdict
from .usage import UsageStore

SOURCES = ("gateway", "mcp", "claude_code", "report", "billing_export", "cloudevents", "seed")
LIMIT_CONTROLS = {"auth", "budget", "resources", "workflow", "access_grant", "model_allowlist", "tool_access"}
CE_PREFIX = "dev.shpont."


def verdict_severity(v: Verdict) -> str:
    if v.action is Action.BLOCK:
        blocker = next((f for f in v.findings if f.action is Action.BLOCK), None)
        return "medium" if blocker is None or blocker.control in LIMIT_CONTROLS else "high"
    return {Action.REDACT: "low", Action.WARN: "low"}.get(v.action, "info")


def verdict_event(ctx: Context, v: Verdict, policy: Policy) -> dict[str, Any]:
    """A gateway check as an event (no text: the audit log keeps the masked text)."""
    resource = policy.model_resource(ctx.model) if ctx.model else None
    if ctx.tool:
        resource = next(iter(policy.grant_resources(ctx.tool)), None) or next(
            (n for n, r in policy.resources.items() if any(t == ctx.tool for t in r.start_tools)), resource
        )
    return {
        "ts": v.ts,
        "id": v.request_id + ":" + ctx.direction.value[:3],
        "source": {"mcp": "mcp", "claude_code": "claude_code"}.get(ctx.channel, "gateway"),
        "kind": f"check.{ctx.direction.value}",
        "principal": ctx.principal.id,
        "team": ctx.principal.team,
        "client": ctx.client,
        "session": ctx.session_id,
        "task": ctx.task_id,
        "workflow": ctx.workflow,
        "resource": resource,
        "urn": policy.catalog[resource].urn if resource in policy.catalog else None,
        "model": ctx.model,
        "tool": ctx.tool,
        "decision": v.action.value,
        "severity": verdict_severity(v),
        "request_id": v.request_id,
        "detail": {
            "channel": ctx.channel,
            "reason": v.reason,
            "workflow_source": ctx.workflow_source,
            "findings": [f"{f.control}/{f.category}:{f.action.value}" for f in v.findings],
        },
    }


def amount(value: Any, name: str) -> float:
    """A cost, token count or quantity: a finite number, never negative (a negative or NaN spend would
    lower or poison the budgets it is charged to)."""
    try:
        x = float(value or 0)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if not math.isfinite(x) or x < 0:
        raise ValueError(f"{name} must be a finite, non-negative number")
    return x


def _ts(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        if not math.isfinite(value):
            raise ValueError("time must be a finite number")
        return float(value) / (1e9 if value > 1e14 else 1e3 if value > 1e11 else 1)  # ns, ms or s
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def from_cloudevent(ce: dict[str, Any]) -> dict[str, Any]:
    """A CloudEvents 1.0 envelope (structured JSON) to a flat event.

    `type` becomes the kind (a `dev.shpont.` prefix is dropped), `subject` the principal, and `data` may use
    OpenTelemetry GenAI names (gen_ai.request.model, gen_ai.usage.input_tokens, gen_ai.tool.name), FOCUS
    names (BilledCost, ConsumedQuantity, ConsumedUnit, ResourceId) or our own (workflow, task, usd...)."""
    for attr in ("specversion", "id", "source", "type"):
        if not ce.get(attr):
            raise ValueError(f"CloudEvent is missing required attribute {attr!r}")
    if str(ce["specversion"]) != "1.0":
        raise ValueError("only CloudEvents specversion 1.0 is supported")
    data = ce.get("data") if isinstance(ce.get("data"), dict) else {}

    def pick(*names: str) -> Any:
        return next((data[n] for n in names if data.get(n) not in (None, "")), None)

    kind = str(ce["type"])
    kind = kind[len(CE_PREFIX) :] if kind.startswith(CE_PREFIX) else kind
    inp = int(amount(pick("gen_ai.usage.input_tokens", "input_tokens"), "input_tokens"))
    out = int(amount(pick("gen_ai.usage.output_tokens", "output_tokens"), "output_tokens"))
    known = {
        "gen_ai.request.model", "model", "gen_ai.usage.input_tokens", "input_tokens", "gen_ai.usage.output_tokens",
        "output_tokens", "gen_ai.tool.name", "tool", "BilledCost", "usd", "cost_usd", "ConsumedQuantity", "quantity",
        "ConsumedUnit", "unit", "ResourceId", "resource", "workflow", "task", "session", "decision", "severity",
        "team", "email", "client", "meter", "prompt_id",
    }  # fmt: skip
    return {
        "ts": _ts(ce.get("time")),
        "id": str(ce["id"])[:64],
        "source": "cloudevents",
        "kind": kind[:80],
        "principal": ce.get("subject") or pick("principal"),
        "email": pick("email", "user.email"),
        "team": pick("team"),
        "client": pick("client") or str(ce["source"])[:120],
        "session": pick("session", "session.id"),
        "prompt_id": pick("prompt_id"),
        "task": pick("task"),
        "workflow": pick("workflow"),
        "resource": pick("ResourceId", "resource"),
        "model": pick("gen_ai.request.model", "model"),
        "tool": pick("gen_ai.tool.name", "tool"),
        "decision": pick("decision"),
        "severity": pick("severity"),
        "usd": amount(pick("BilledCost", "usd", "cost_usd"), "usd"),
        "input_tokens": inp,
        "output_tokens": out,
        "quantity": amount(pick("ConsumedQuantity", "quantity"), "quantity"),
        "unit": pick("ConsumedUnit", "unit") or "",
        "meter": bool(data.get("meter", True)),
        "detail": {k: v for k, v in data.items() if k not in known} or None,
    }


class Ingestor:
    """Normalizes an event, attributes it, and writes it to the activity stream (and the ledger if metered)."""

    def __init__(self, store: UsageStore, ledger: BudgetLedger, policies: PolicyStore):
        self.store = store
        self.ledger = ledger
        self.policies = policies
        self.listeners: list[Any] = []  # callables(event) - detections hook in here

    def ingest(self, evt: dict[str, Any], policy: Policy | None = None, dedupe: bool = False) -> dict[str, Any]:
        """With `dedupe`, an event whose id this producer already sent for this person is not counted again
        (CloudEvents delivery is at-least-once: retries resend the same id)."""
        p = policy or self.policies.policy
        e = dict(evt)
        ts = e.get("ts")
        e["ts"] = ts if isinstance(ts, int | float) and math.isfinite(ts) and ts > 0 else time.time()
        e["source"] = e.get("source") or "gateway"
        # Identity join: an id or an email from the directory; telemetry may only know one of them.
        ident = p.identity_of(e.get("principal"), e.get("email"))
        if ident:
            e["principal"] = ident.principal
            e["team"] = e.get("team") or ident.team
        e["principal"] = e.get("principal") or "unattributed"
        e["team"] = e.get("team") or "unattributed"
        if dedupe and e.get("id") and self.store.has_event(e["id"], e["source"], e["principal"], e.get("client")):
            return {**e, "duplicate": True}
        # Resource by name, URN or model.
        ref = e.get("resource")
        name = resolve(p, ref) if isinstance(ref, str) and ref else None
        if name is None and e.get("model"):
            name = p.model_resource(e["model"])
        if name:
            e["resource"], e["urn"] = name, p.catalog[name].urn
        elif ref:
            e["urn"] = ref if str(ref).startswith("urn:") else None
        if e.get("task") and not e.get("workflow"):
            e["workflow"] = self.store.workflow_of_task(e["principal"], e["task"])
        # Every producer (telemetry, CloudEvents, hooks) passes through here: refuse amounts that would
        # lower or poison a budget before anything is written.
        inp, out = (
            int(amount(e.get("input_tokens"), "input_tokens")),
            int(amount(e.get("output_tokens"), "output_tokens")),
        )
        e["quantity"] = amount(e.get("quantity"), "quantity") or None
        e["tokens"] = int(amount(e.get("tokens"), "tokens")) or (inp + out) or None
        e["usd"] = amount(e.get("usd"), "usd") or None
        if e.get("meter") and (e["usd"] or inp or out or e.get("quantity")):
            entry = p.catalog.get(name or "")
            usd = e["usd"] or 0.0
            if not usd and entry:  # priced from the catalog when the producer sent no cost
                pr = entry.price
                usd = inp * pr.usd_per_1m_input / 1e6 + out * pr.usd_per_1m_output / 1e6
                usd += float(e.get("quantity") or 0) * pr.usd_per_unit
                e["usd"] = usd or None
            self.store.add(
                principal=e["principal"],
                team=e["team"],
                resource=name or str(e.get("resource") or "external"),
                workflow=e.get("workflow"),
                task=e.get("task"),
                session=e.get("session"),
                model=e.get("model"),
                requests=1 if (inp or out) else 0,
                input_tokens=inp,
                output_tokens=out,
                quantity=float(e.get("quantity") or 0),
                unit=e.get("unit") or (entry.unit if entry else ""),
                usd=usd,
                request_id=e.get("request_id") or e.get("id"),
                ts=e["ts"],
                source=e["source"],
                client=e.get("client"),
            )
            self.ledger.absorb(e["principal"], e["team"], usd, e["ts"], inp + out)
        row = self.store.add_event(e)
        for fn in self.listeners:
            fn(row, p)
        return row
