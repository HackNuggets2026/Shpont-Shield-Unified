"""Small durable state that changes at runtime: agent grants, manual watch levels, external risk
signals, suspensions.

The policy file is security's source of truth and is never written by the gateway; this is
what employees and security staff change from the panels.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self.data: dict[str, Any] = {"grants": {}, "watch": {}, "signals": {}, "suspended": {}}
        self.version = 0  # bumped on every change; part of the verdict cache key
        if self.path and self.path.exists():
            self.data.update(json.loads(self.path.read_text()))

    @property
    def grants(self) -> dict[str, dict[str, dict]]:
        return self.data["grants"]

    @property
    def watch(self) -> dict[str, dict]:
        return self.data["watch"]

    @property
    def signals(self) -> dict[str, dict[str, dict]]:
        return self.data["signals"]  # principal -> source -> signal

    @property
    def suspended(self) -> dict[str, dict]:
        return self.data["suspended"]

    def save(self) -> None:
        self.version += 1
        if not self.path:
            return
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1))
            os.replace(tmp, self.path)  # atomic: a crash never leaves half a file
