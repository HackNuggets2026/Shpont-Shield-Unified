"""Fire the corpus at a target."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..core.target import Target
from ..core.types import Case, Outcome


@dataclass
class Run:
    fingerprint: str
    outcomes: list[Outcome]
    started: float = field(default_factory=time.time)
    seconds: float = 0.0

    def by_id(self) -> dict[str, Outcome]:
        return {o.case.id: o for o in self.outcomes}


def run(target: Target, cases: list[Case], fingerprint: str | None = None, workers: int = 8) -> Run:
    t = time.perf_counter()
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        decisions = list(pool.map(target.evaluate, cases))
    outcomes = [Outcome(c, d) for c, d in zip(cases, decisions, strict=True)]
    return Run(fingerprint or target.fingerprint(), outcomes, started, time.perf_counter() - t)
