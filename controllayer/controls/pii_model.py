"""Contextual PII from a token classifier (OpenAI Privacy Filter sidecar), plus overrides."""

from __future__ import annotations

import asyncio
import math
import re
from typing import Any, Protocol

import httpx

from ..config import Policy
from ..decision import DecisionBackend
from ..types import Action, Context, Direction, Finding, Span
from .base import applies, finding

PII_CONTROLS = ("pii", "pii_model")


class Detector(Protocol):
    async def detect(self, text: str) -> list[dict[str, Any]]: ...


class PrivacyFilterClient:
    def __init__(self, url: str, timeout: float, client: httpx.AsyncClient | None = None):
        self.url = url.rstrip("/") + "/detect"
        self.timeout = timeout
        self.client = client or httpx.AsyncClient()

    async def detect(self, text: str) -> list[dict[str, Any]]:
        r = await self.client.post(self.url, json={"text": text}, timeout=self.timeout)
        r.raise_for_status()
        spans = r.json()["spans"]
        if not isinstance(spans, list):
            raise ValueError("spans must be a list")
        return spans


class StubDetector:
    """Offline stand-in so the pipeline can be demonstrated without the model. Not a classifier."""

    _PATTERNS = [
        (
            "private_person",
            re.compile(
                r"(?:(?:my name is|name:|customer|client|Mr\.?|Mrs\.?|Ms\.?|Dr\.?)\s+)([A-Z][a-z]+(?: [A-Z][a-z]+)+)"
            ),
        ),
        (
            "private_address",
            re.compile(r"\b(?:ul\.|ulica)\s+[A-Z]\w+(?: \w+)?\s+\d+\w*(?:/\d+)?(?:,\s*\d{2}-\d{3}\s+[A-Z]\w+)?"),
        ),
        ("private_address", re.compile(r"\b\d+\s+[A-Z]\w+(?: [A-Z]\w+)*\s+(?:Street|St\.|Avenue|Ave\.|Road|Rd\.)")),
    ]

    async def detect(self, text: str) -> list[dict[str, Any]]:
        out = []
        for label, pat in self._PATTERNS:
            for m in pat.finditer(text):
                g = 1 if m.groups() else 0
                out.append({"label": label, "start": m.start(g), "end": m.end(g), "score": 0.9})
        return out


def detector_for(policy: Policy, http: httpx.AsyncClient | None) -> Detector | None:
    cfg = policy.pii_model
    if cfg.backend == "privacy_filter":
        return PrivacyFilterClient(cfg.url, cfg.timeout_seconds, http)
    if cfg.backend == "stub":
        return StubDetector()
    return None


async def check(ctx: Context, policy: Policy, detector: Detector | None) -> list[Finding]:
    cfg = policy.pii_model
    if detector is None or not applies(cfg, ctx) or not ctx.text.strip():
        return []
    try:
        raw = await asyncio.wait_for(detector.detect(ctx.text), cfg.timeout_seconds)
        by_label: dict[str, list[Span]] = {}
        for s in raw:
            score, start, end = float(s["score"]), int(s["start"]), int(s["end"])
            if not math.isfinite(score) or not 0 <= start < end <= len(ctx.text):
                raise ValueError(f"bad span {s!r}")
            if score >= cfg.min_score and s["label"] in cfg.entities:
                by_label.setdefault(str(s["label"]), []).append(Span(start, end, str(s["label"])))
    except Exception as e:  # noqa: BLE001 - detector failure follows fail_mode, never a 500
        action = Action.BLOCK if cfg.fail_mode == "closed" else Action.LOG
        return [Finding("pii_model", "detector_unavailable", action, action, detail=f"fail-{cfg.fail_mode}: {e}")]
    return [
        finding(
            "pii_model", cfg, label, cfg.entities[label], detail=f"{len(spans)} x {label}", spans=spans, tier="model"
        )
        for label, spans in by_label.items()
    ]


def _liftable(f: Finding, ceiling: Action) -> bool:
    return f.control in PII_CONTROLS and f.action is not Action.ALLOW and f.action.rank <= ceiling.rank


async def apply_overrides(
    ctx: Context, policy: Policy, findings: list[Finding], backend: DecisionBackend | None
) -> list[Finding]:
    """Downgrade PII findings to `log` when the employee (permitted roles, with a reason) or the
    decision model (the PII is needed for the task) says so. Never lifts past `override_max`."""
    cfg = policy.pii_model
    targets = [f for f in findings if _liftable(f, cfg.override_max) and f.action.rank >= Action.REDACT.rank]
    if not targets:
        return findings
    if ctx.pii_override is not None:
        if ctx.principal.role not in cfg.override_roles:
            return findings + [
                Finding(
                    "pii_override",
                    "denied",
                    Action.LOG,
                    Action.LOG,
                    detail=f"role {ctx.principal.role!r} may not override PII masking",
                )
            ]
        for f in targets:
            f.action = Action.LOG
            f.detail += f" [overridden by user: {ctx.pii_override[:200]}]"
        return findings
    mo = cfg.model_override
    targets = [f for f in targets if f.control == "pii_model" and f.category in mo.labels]
    if not targets or not mo.enabled or backend is None or ctx.direction is not Direction.INPUT:
        return findings
    q = {"pii_necessary": {"type": "noul", "instructions": mo.instructions}}
    try:
        res = await backend.decide(
            policy.semantic.fast_model,
            {"content_role": "a request an employee is sending to an AI model", "content": ctx.text},
            q,
            {"pii_necessary": mo.keywords},
        )
        p = res.answers["pii_necessary"].p
    except Exception:  # noqa: BLE001 - no verdict means no override; masking stays
        return findings
    if p >= mo.threshold:
        for f in targets:
            f.action = Action.LOG
            f.detail += f" [overridden by model: needed for task, p={p:.2f}]"
    return findings
