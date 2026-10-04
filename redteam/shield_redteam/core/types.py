"""Shared vocabulary. Every module (Poligon, Tripwire, and the planned ones) speaks these types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The control layer's enforcement ladder, weakest first. Redact and block both stop the payload.
LADDER = ("allow", "log", "warn", "redact", "block")
STOPPING = frozenset({"redact", "block"})
DIRECTIONS = ("input", "output", "tool_call", "tool_result", "tool_description")


@dataclass(frozen=True)
class Case:
    """One self-test case. `attack` cases must be stopped, `benign` cases must pass."""

    id: str
    kind: str  # attack | benign
    category: str
    text: str
    direction: str = "input"
    principal: str = "alice"
    owasp: str = ""
    note: str = ""
    parent: str | None = None  # set on mutants
    technique: str | None = None  # mutation that produced it
    # False where rewriting the text would destroy the payload itself (a key, a card number).
    mutable: bool = True

    @property
    def must_stop(self) -> bool:
        return self.kind == "attack"


@dataclass
class Decision:
    """What a target answered for one payload."""

    action: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0
    error: str = ""

    @property
    def stopped(self) -> bool:
        return self.action in STOPPING


@dataclass
class Outcome:
    case: Case
    decision: Decision

    @property
    def ok(self) -> bool:
        return not self.decision.error and self.decision.stopped == self.case.must_stop

    @property
    def caught_by(self) -> list[str]:
        return sorted({f"{f.get('control')}/{f.get('category')}" for f in self.decision.findings})
