"""Poligon: the layer attacks itself, continuously, and keeps score.

Every tick it asks the target for its configuration fingerprint. On a change (or on the
periodic timer) it re-fires the corpus and, if any case flipped, records the impact of the change.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from ..core.target import Target
from ..core.types import Case
from .mutate import mutants
from .runner import Run, run
from .score import impact, report


class Poligon:
    def __init__(
        self, target: Target, cases: list[Case], mutate: bool = True, interval: float = 30.0, workers: int = 8
    ):
        self.target = target
        self.base_cases = cases
        self.cases = cases + (mutants(cases) if mutate else [])
        self.interval = interval
        self.workers = workers
        self.last: Run | None = None
        self.last_report: dict[str, Any] | None = None
        self.history: deque[dict[str, Any]] = deque(maxlen=120)
        self.changes: deque[dict[str, Any]] = deque(maxlen=40)
        self.error = ""
        self.runs = 0
        self._lock = threading.Lock()
        self._next_timed = 0.0

    def run_now(self, fingerprint: str | None = None) -> dict[str, Any]:
        with self._lock:
            current = run(self.target, self.cases, fingerprint, self.workers)
            rep = report(current)
            if self.last is not None:
                delta = impact(self.last, current)
                if delta["changed"] or delta["from"] != delta["to"]:
                    self.changes.appendleft(delta)
            self.last, self.last_report = current, rep
            self.runs += 1
            self.history.append(
                {
                    "ts": current.started,
                    "posture": rep["posture"],
                    "protection": rep["protection"],
                    "friction": rep["friction"],
                    "evasion": rep["evasion"],
                    "fingerprint": current.fingerprint,
                }
            )
            self._next_timed = time.monotonic() + self.interval
            return rep

    def tick(self) -> bool:
        """Run if the target's configuration changed or the timer expired. Returns True if it ran."""
        try:
            fp = self.target.fingerprint()
        except Exception as e:  # noqa: BLE001 - an unreachable target is a state to show, not a crash
            self.error = f"target unreachable: {type(e).__name__}: {e}"
            return False
        self.error = ""
        if self.last is None or fp != self.last.fingerprint or time.monotonic() >= self._next_timed:
            self.run_now(fp)
            return True
        return False

    def state(self) -> dict[str, Any]:
        return {
            "report": self.last_report,
            "history": list(self.history),
            "changes": list(self.changes),
            "runs": self.runs,
            "error": self.error,
            "corpus": {
                "cases": len(self.base_cases),
                "mutants": len(self.cases) - len(self.base_cases),
                "attacks": sum(c.must_stop for c in self.base_cases),
                "benign": sum(not c.must_stop for c in self.base_cases),
            },
        }
