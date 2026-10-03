"""AI-based controls: policy-defined questions answered by a tiered decision-model cascade.

Tier 1 (fast model) answers every applicable question for every chunk. Answers that land
in the uncertain band, or with low confidence, are re-asked to the deep model.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from ..config import Policy, SemanticControl
from ..decision import Answer, DecisionBackend, DecisionError
from ..types import Action, Context, Finding
from .base import applies, finding

_ROLE = {
    "input": "a prompt a user or agent is sending to an AI model",
    "output": "a response an AI model produced",
    "tool_call": "arguments an AI agent is passing to a tool",
    "tool_result": "data a tool returned to an AI agent",
    "tool_description": "the description of a tool offered to an AI agent",
}


@dataclass
class SemanticStats:
    fast_ms: float = 0.0
    deep_ms: float = 0.0
    escalated: list[str] = field(default_factory=list)
    chunks: int = 0
    input_tokens: int = 0
    error: str | None = None


def chunk(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    overlap = min(200, size // 10)  # an injection straddling a boundary still lands whole in one chunk
    return [text[i : i + size] for i in range(0, len(text), size - overlap)]


def _question(c: SemanticControl) -> dict:
    q: dict = {"type": c.type, "instructions": c.instructions}
    if c.type == "choice":
        q["criteria"] = c.criteria
    return q


def _hints(controls: dict[str, SemanticControl]) -> dict[str, list[str]]:
    h: dict[str, list[str]] = {}
    for name, c in controls.items():
        h[name] = c.keywords
        for opt, kws in c.option_keywords.items():
            h[f"{name}.{opt}"] = kws
    return h


def _proposed(c: SemanticControl, a: Answer) -> Action:
    if c.type == "noul":
        return c.thresholds.action_for(a.p)
    return c.actions.get(a.choice or "", Action.ALLOW)


def _worst(c: SemanticControl, answers: list[Answer]) -> Answer:
    if c.type == "noul":
        return max(answers, key=lambda a: a.p)
    return max(answers, key=lambda a: _proposed(c, a).rank)


class SemanticGuard:
    def __init__(self, backend: DecisionBackend):
        self.backend = backend

    async def _ask(self, model: str, chunks: list[str], ctx: Context, controls, stats: SemanticStats):
        questions = {n: _question(c) for n, c in controls.items()}
        hints = _hints(controls)
        # Content travels as a JSON field so text inside it reads as data, not as the judge's task.
        states = [{"content_role": _ROLE[ctx.direction.value], "content": ch} for ch in chunks]
        results = await asyncio.gather(*(self.backend.decide(model, s, questions, hints) for s in states))
        stats.input_tokens += sum(r.input_tokens for r in results)
        return {n: _worst(c, [r.answers[n] for r in results]) for n, c in controls.items()}

    async def check(self, ctx: Context, policy: Policy) -> tuple[list[Finding], SemanticStats]:
        eng = policy.semantic
        stats = SemanticStats()
        controls = {n: c for n, c in policy.semantic_controls.items() if applies(c, ctx)}
        if eng.backend == "off" or not controls or not ctx.text.strip():
            return [], stats
        chunks = chunk(ctx.text, eng.max_chunk_chars)
        stats.chunks = len(chunks)
        tiers: dict[str, str] = {}
        try:
            t0 = time.perf_counter()
            answers = await asyncio.wait_for(self._ask(eng.fast_model, chunks, ctx, controls, stats), eng.timeout_seconds)
            stats.fast_ms = (time.perf_counter() - t0) * 1000
            tiers = dict.fromkeys(answers, "fast")
            lo, hi = eng.escalate_band
            unsure = {
                n: c
                for n, c in controls.items()
                if (c.type == "noul" and lo <= answers[n].p < hi) or answers[n].confidence < eng.min_confidence
            }
            if unsure and eng.deep_model and eng.deep_model != eng.fast_model:
                t1 = time.perf_counter()
                deep = await asyncio.wait_for(self._ask(eng.deep_model, chunks, ctx, unsure, stats), eng.timeout_seconds)
                stats.deep_ms = (time.perf_counter() - t1) * 1000
                answers.update(deep)
                tiers.update(dict.fromkeys(deep, "deep"))
                stats.escalated = sorted(unsure)
        except (DecisionError, asyncio.TimeoutError) as e:
            stats.error = str(e) or type(e).__name__
            action = Action.BLOCK if eng.fail_mode == "closed" else Action.LOG
            return [
                Finding(
                    control="semantic_engine",
                    category="engine_unavailable",
                    action=action,
                    proposed=action,
                    detail=f"fail-{eng.fail_mode}: {stats.error}",
                )
            ], stats

        out = []
        for name, c in controls.items():
            a = answers[name]
            proposed = _proposed(c, a)
            if proposed is Action.ALLOW:
                continue
            label = f"p={a.p:.2f}" if c.type == "noul" else f"{a.choice} p={a.p:.2f}"
            out.append(
                finding(
                    name,
                    c,
                    a.choice or name,
                    proposed,
                    score=a.p,
                    detail=f"{label} conf={a.confidence:.2f}",
                    tier=tiers[name],
                )
            )
        return out, stats
