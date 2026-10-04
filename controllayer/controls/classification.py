"""Per-model classification ceilings: the same prompt goes to an on-prem model and is refused by a cloud one.

Deterministic and keyword-based: `confidentiality.topics` in the policy name the phrases that make a text
internal, confidential or restricted; the catalog's `max_classification` on each ai_model entry is the highest
level that model may receive. Over the ceiling the prompt is blocked (the control's mode); within it the
match is logged, so the audit shows what classified data went to which model.
"""

from __future__ import annotations

import re
from functools import lru_cache

from ..config import CLASSIFICATIONS, Policy
from ..types import Action, Context, Finding, Span
from .base import applies, finding

LEVEL = {c: i for i, c in enumerate(CLASSIFICATIONS)}


@lru_cache(maxsize=256)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def classify(text: str, policy: Policy) -> tuple[str, str | None, list[Span]]:
    """(classification, topic id, matched spans) of the highest-level topic the text mentions."""
    best: tuple[str, str | None, list[Span]] = ("public", None, [])
    for tid, topic in policy.confidentiality.topics.items():
        spans = [
            Span(m.start(), m.end(), f"topic:{tid}") for k in topic.keywords for m in _compiled(k).finditer(text)
        ]
        if spans and LEVEL[topic.classification] > LEVEL[best[0]]:
            best = (topic.classification, tid, spans)
    return best


def ceiling(policy: Policy, model: str | None) -> str | None:
    rid = policy.model_resource(model)
    return policy.catalog[rid].max_classification if rid else None


def check(ctx: Context, policy: Policy) -> list[Finding]:
    cfg = policy.confidentiality
    if not ctx.model or not applies(cfg, ctx):
        return []
    level, tid, spans = classify(ctx.text, policy)
    if tid is None:
        return []
    limit = ceiling(policy, ctx.model)
    over = limit is not None and LEVEL[level] > LEVEL[limit]
    detail = f"{level} topic {tid!r}; model {ctx.model!r} accepts up to {limit or 'any level'}"
    return [finding("confidentiality", cfg, tid, Action.BLOCK if over else Action.LOG, detail=detail, spans=spans)]
