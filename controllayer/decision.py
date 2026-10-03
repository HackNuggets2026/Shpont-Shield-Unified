"""Clients for decision models (Ollama /v1/systemone: nimble, tev1) behind one interface."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


@dataclass
class Answer:
    type: str
    p: float  # noul: P(yes); choice: probability of the chosen option
    choice: str | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 1.0


@dataclass
class DecisionResult:
    answers: dict[str, Answer]
    input_tokens: int = 0


class DecisionError(Exception):
    pass


class DecisionBackend(Protocol):
    name: str

    async def decide(
        self, model: str, state: Any, questions: dict[str, dict], hints: dict[str, list[str]]
    ) -> DecisionResult: ...


def _noul_confidence(p: float) -> float:
    # Distance from a coin flip, scaled to 0..1 (Ollama only returns confidence for choice/score).
    return abs(p - 0.5) * 2


class OllamaSystemOne:
    name = "ollama"

    def __init__(self, url: str, timeout: float, keep_alive: str, client: httpx.AsyncClient | None = None):
        self.url = url.rstrip("/") + "/v1/systemone"
        self.timeout = timeout
        self.keep_alive = keep_alive
        self.client = client or httpx.AsyncClient()

    async def decide(self, model, state, questions, hints) -> DecisionResult:
        body = {"model": model, "state": state, "questions": questions, "keep_alive": self.keep_alive}
        try:
            r = await self.client.post(self.url, json=body, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise DecisionError(f"{model}: {type(e).__name__}: {e}") from e
        if r.status_code != 200:
            raise DecisionError(f"{model}: HTTP {r.status_code}: {r.text[:200]}")
        try:
            return _parse(r.json(), questions)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise DecisionError(f"{model}: malformed response: {type(e).__name__}: {e}") from e


def _prob(x: Any) -> float:
    p = float(x)
    if not 0.0 <= p <= 1.0:  # also rejects NaN
        raise ValueError(f"probability out of range: {x!r}")
    return p


def _parse(data: dict, questions: dict[str, dict]) -> DecisionResult:
    """Strict: anything unexpected raises, so the caller's fail_mode decides, never a silent allow."""
    answers = {}
    for name, a in data["answers"].items():
        if name not in questions:
            continue
        if a["type"] == "noul":
            p = _prob(a["noul"])
            answers[name] = Answer("noul", p, confidence=_prob(a.get("confidence", _noul_confidence(p))))
        elif a["type"] == "choice":
            choice = a["choice"]
            if choice not in questions[name].get("criteria", {}):
                raise ValueError(f"{name}: unknown option {choice!r}")
            probs = {k: _prob(v) for k, v in a.get("probabilities", {}).items()}
            answers[name] = Answer("choice", probs.get(choice, 1.0), choice, probs, _prob(a.get("confidence", 1.0)))
    missing = set(questions) - set(answers)
    if missing:
        raise ValueError(f"no answer for {sorted(missing)}")
    return DecisionResult(answers, int(data.get("usage", {}).get("input_tokens", 0)))


class HeuristicBackend:
    """Keyword stand-in for machines that cannot run a decision model. Not a real classifier."""

    name = "heuristic"

    async def decide(self, model, state, questions, hints) -> DecisionResult:
        text = (state.get("content", "") if isinstance(state, dict) else str(state)).lower()
        answers = {}
        for name, q in questions.items():
            if q["type"] == "noul":
                hits = sum(1 for k in hints.get(name, []) if re.search(k, text))
                p = {0: 0.03, 1: 0.9}.get(hits, 0.97)
                answers[name] = Answer("noul", p, confidence=_noul_confidence(p))
            else:
                options = list(q["criteria"])
                chosen = options[0]
                for opt in options[1:]:
                    if any(re.search(k, text) for k in hints.get(f"{name}.{opt}", [])):
                        chosen = opt
                        break
                probs = {o: (0.9 if o == chosen else 0.1 / (len(options) - 1)) for o in options}
                answers[name] = Answer("choice", 0.9, chosen, probs, 0.8)
        return DecisionResult(answers, len(text) // 4)


class ScriptedBackend:
    """Test double: first rule whose needle occurs in the content wins.

    Rule values are P(yes) for noul questions and the chosen option for choice questions.
    """

    name = "scripted"

    def __init__(
        self,
        rules: dict[str, list[tuple[str, float | str]]] | None = None,
        default: float = 0.02,
        per_model: dict[str, dict[str, list[tuple[str, float | str]]]] | None = None,
    ):
        self.rules = rules or {}
        self.per_model = per_model or {}
        self.default = default
        self.calls: list[tuple[str, list[str]]] = []
        self.fail = False

    async def decide(self, model, state, questions, hints) -> DecisionResult:
        if self.fail:
            raise DecisionError("scripted failure")
        self.calls.append((model, sorted(questions)))
        text = state.get("content", "") if isinstance(state, dict) else str(state)
        rules = self.per_model.get(model, self.rules)
        answers = {}
        for name, q in questions.items():
            hit = next((v for needle, v in rules.get(name, []) if needle in text), None)
            if q["type"] == "noul":
                p = self.default if hit is None else float(hit)
                answers[name] = Answer("noul", p, confidence=_noul_confidence(p))
            else:
                chosen = hit if isinstance(hit, str) else next(iter(q["criteria"]))
                answers[name] = Answer("choice", 0.9, chosen, {chosen: 0.9}, 0.9)
        return DecisionResult(answers, len(text) // 4)
