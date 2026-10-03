"""Runs the dashboard scripts in QuickJS with a minimal fake DOM, against a TestClient.

No browser exists here: layout, CSS and real event dispatch are not exercised. What is: every script
loads, the router renders each view from live API answers, links and actions go through the real
click handlers, and nothing throws (a failed load or action renders a Primer flash, which the
checks look for).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import quickjs
from fastapi.testclient import TestClient

DASHBOARD = Path(__file__).resolve().parent.parent / "controllayer" / "dashboard"
SCRIPTS = ["app.js", "views/overview.js", "views/people.js", "views/person.js", "views/admin.js", "core.js"]

SHIM = (Path(__file__).parent / "jsconsole_shim.js").read_text()


class Console:
    def __init__(self, client: TestClient, url: str):
        self.client = client
        self.requests: list[str] = []
        self.js = quickjs.Context()
        self.js.add_callable("__fetch", self._fetch)
        self.js.eval(SHIM)
        self.js.eval(f"__go({json.dumps(url)})")
        for name in SCRIPTS:
            self.js.eval((DASHBOARD / name).read_text())
        self.js.eval("__fire('DOMContentLoaded', {})")
        self.settle()

    def _fetch(self, method: str, path: str, body: str, headers: str) -> str:
        self.requests.append(f"{method} {path}")
        r = self.client.request(method, path, content=body or None, headers=json.loads(headers))
        return json.dumps({"status": r.status_code, "body": r.text})

    def settle(self) -> float:
        """Run queued promise jobs (fetches answer synchronously); returns the seconds it took."""
        t = time.perf_counter()
        for _ in range(100_000):
            if not self.js.execute_pending_job():
                break
        return time.perf_counter() - t

    @property
    def html(self) -> str:
        return self.js.eval("document._root.innerHTML")

    @property
    def search(self) -> str:
        return self.js.eval("location.search")

    def nav(self, href: str) -> float:
        """Click a link carrying data-nav."""
        self.js.eval(
            f"__fire('click', {{ button: 0, preventDefault: function () {{}}, target: __target(function (sel) {{"
            f" return sel === 'a[data-nav]' ? __el('A', {{ href: {json.dumps(href)} }}) : null; }}) }})"
        )
        return self.settle()

    def act(self, dataset: dict[str, str], tag: str = "BUTTON") -> float:
        """Click a control carrying data-act (and its other data-* attributes)."""
        self.js.eval(
            f"__fire('click', {{ button: 0, preventDefault: function () {{}}, target: __target(function (sel) {{"
            f" return sel === '[data-act]' ? __el({json.dumps(tag)}, {{}}, {json.dumps(dataset)}) : null; }}) }})"
        )
        return self.settle()

    def type(self, key: str, value: str) -> float:
        """Type into an input carrying data-input, then let its debounce fire."""
        target = f"__el('INPUT', {{ value: {json.dumps(value)} }}, {{ input: {json.dumps(key)} }})"
        self.js.eval(f"__fire('input', {{ target: {target} }})")
        self.js.eval("__timers.splice(0).forEach(function (f) { f(); })")
        return self.settle()

    def change(self, dataset: dict[str, str], value: str) -> float:
        """Pick a value in a select carrying data-param or data-act."""
        self.js.eval(
            f"__fire('change', {{ target: __el('SELECT', {{ value: {json.dumps(value)} }}, {json.dumps(dataset)}) }})"
        )
        return self.settle()

    def errors(self) -> list[str]:
        """Failures the page shows (flash-error) or logged."""
        found = json.loads(self.js.eval("JSON.stringify(__errors)"))
        if "flash-error" in self.html:
            start = self.html.index("flash-error")
            found.append(self.html[start : start + 300])
        return found
