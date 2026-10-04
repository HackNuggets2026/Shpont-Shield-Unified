"""Self-test: the scenarios in scenarios.yaml run in-process through the same path as POST /admin/try.

Each step is evaluated as its principal on the dashboard channel, unmetered and unscored, so a run never
spends anyone's budget, raises their risk or opens an incident. A run is keyed by the policy version it
ran on; when the policy changes (file edit or admin overlay), the scenarios run again after a short
debounce and the result names the steps that passed before and fail now.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from .config import Policy, PolicyStore
from .controls.access import by_principal
from .types import Context, Direction

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
HISTORY = 20
DEBOUNCE_SECONDS = 2.0


def scenarios_path(store: PolicyStore) -> Path:
    """ACL_SCENARIOS, else scenarios.yaml next to the policy, else the one shipped with the code."""
    env = os.environ.get("ACL_SCENARIOS")
    if env:
        return Path(env)
    beside = store.base_dir / "scenarios.yaml"
    return beside if beside.exists() else ROOT / "scenarios.yaml"


def load_scenarios(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text()) or {}
    items = data.get("scenarios", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError(f"{path}: expected a list of scenarios")
    out = []
    for sc in items:
        if not isinstance(sc, dict) or not sc.get("id") or not isinstance(sc.get("steps"), list):
            raise ValueError(f"{path}: every scenario needs an id and a list of steps")
        for st in sc["steps"]:
            Direction(st.get("direction", "input"))  # raises on a typo
            expect = st.get("expect") or ["allow"]
            st["expect"] = [expect] if isinstance(expect, str) else list(expect)
        out.append(sc)
    return out


def _step_text(step: dict[str, Any]) -> str:
    if step.get("text") is not None:
        return str(step["text"])
    args = step.get("arguments")
    return json.dumps(args, ensure_ascii=False) if args is not None else ""


class SelfTest:
    def __init__(self, layer: Any, store: PolicyStore, path: Path | None = None):
        self.layer = layer
        self.store = store
        self.path = path or scenarios_path(store)
        self.history_path = store.data_path("data/selftest.json")
        self.history: list[dict[str, Any]] = self._load_history()
        self.auto = os.environ.get("ACL_SELFTEST_AUTO", "1") != "0"
        self.debounce = DEBOUNCE_SECONDS
        self.loop: asyncio.AbstractEventLoop | None = None  # the app's loop, set at startup
        self.pending: dict[str, Any] | None = None  # {"from": old version, "since": ts} while a run is queued
        self.running = False
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        store.listeners.append(self.on_reload)

    # ---- history ----------------------------------------------------------------------------

    def _load_history(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.history_path.read_text())
            return data[-HISTORY:] if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save_history(self) -> None:
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.history_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.history[-HISTORY:]))
            tmp.replace(self.history_path)
        except OSError as e:
            log.error("self-test history not saved: %s", e)

    def latest(self) -> dict[str, Any] | None:
        return self.history[-1] if self.history else None

    def status(self) -> dict[str, Any]:
        return {
            "auto": self.auto,
            "debounce_seconds": self.debounce,
            "running": self.running,
            "pending": self.pending,
            "policy_version": self.store.policy.version,
            "scenarios_file": str(self.path),
        }

    # ---- running ----------------------------------------------------------------------------

    async def _step(self, policy: Policy, step: dict[str, Any], run_id: str) -> dict[str, Any]:
        principal = by_principal(policy, step.get("principal"))
        ctx = Context(
            principal,
            Direction(step.get("direction", "input")),
            _step_text(step),
            model=step.get("model"),
            tool=step.get("tool"),
            tool_args=step.get("arguments"),
            channel="dashboard",
            metered=False,
            scored=False,
        )
        t0 = time.perf_counter()
        error = None
        try:
            v = await self.layer.evaluate(ctx, extra={"selftest": run_id}, audit_allow=False)
            actual, reason = v.action.value, v.reason
            controls = sorted({f.control for f in v.findings if not f.shadow})
            findings = [
                {"control": f.control, "category": f.category, "action": f.action.value, "shadow": f.shadow}
                for f in v.findings
            ]
        except Exception as e:  # noqa: BLE001 - one broken step must not stop the run
            actual, reason, controls, findings, error = "error", str(e), [], [], str(e)
        latency = (time.perf_counter() - t0) * 1000
        expect = step["expect"]
        passed = actual in expect
        if passed and step.get("control") and actual != "allow":
            passed = step["control"] in controls
        return {
            "principal": step.get("principal"),
            "authenticated": principal.authenticated,
            "direction": ctx.direction.value,
            "model": step.get("model"),
            "tool": step.get("tool"),
            "arguments": step.get("arguments"),
            "text": ctx.text,
            "why": step.get("why", ""),
            "expect": expect,
            "control": step.get("control"),
            "known_gap": bool(step.get("known_gap")),
            "actual": actual,
            "reason": reason,
            "controls": controls,
            "findings": findings,
            "error": error,
            "passed": passed,
            "latency_ms": round(latency, 2),
        }

    async def run(self, trigger: str = "manual", prev_version: str | None = None) -> dict[str, Any]:
        """Run every scenario once against the live policy, record the result and return it."""
        self.running = True
        try:
            return await self._run(trigger, prev_version)
        finally:
            self.running = False

    async def _run(self, trigger: str, prev_version: str | None) -> dict[str, Any]:
        policy = self.store.policy
        run_id = uuid.uuid4().hex[:12]
        started = time.time()
        t0 = time.perf_counter()
        try:
            scenarios = load_scenarios(self.path)
            error = None
        except (OSError, ValueError, yaml.YAMLError) as e:
            scenarios, error = [], f"scenarios not loaded: {e}"
        out = []
        for sc in scenarios:
            steps = []
            for st in sc["steps"]:
                r = await self._step(policy, st, run_id)
                r["known_gap"] = r["known_gap"] or bool(sc.get("known_gap"))
                steps.append(r)
            out.append(
                {
                    "id": sc["id"],
                    "title": sc.get("title", sc["id"]),
                    "why": sc.get("why", ""),
                    "known_gap": bool(sc.get("known_gap")),
                    "passed": all(s["passed"] or s["known_gap"] for s in steps),
                    "steps": steps,
                }
            )
        prev = self.history[-1] if self.history else None
        if prev_version is None and prev is not None and prev["policy_version"] != policy.version:
            prev_version = prev["policy_version"]
        regressions = _regressions(prev, out) if prev else []
        all_steps = [s for sc in out for s in sc["steps"]]
        summary = {
            "scenarios": len(out),
            "scenarios_passed": sum(1 for sc in out if sc["passed"]),
            "steps": len(all_steps),
            "passed": sum(1 for s in all_steps if s["passed"]),
            "failed": sum(1 for s in all_steps if not s["passed"] and not s["known_gap"]),
            "known_gaps": sum(1 for s in all_steps if not s["passed"] and s["known_gap"]),
            "regressions": len(regressions),
        }
        result = {
            "run_id": run_id,
            "trigger": trigger,
            "policy_version": policy.version,
            "prev_version": prev_version,
            "started_at": started,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "error": error,
            "summary": summary,
            "regressions": regressions,
            "note": _note(prev_version, policy.version, regressions),
            "scenarios": out,
        }
        self.history.append(result)
        del self.history[:-HISTORY]
        self._save_history()
        log.info("self-test %s on policy %s: %s", trigger, policy.version, summary)
        return result

    # ---- auto-run on policy change ----------------------------------------------------------

    def on_reload(self, old: str, new: Policy) -> None:
        """A store listener: (re)start the debounce; the run happens once edits settle."""
        if not self.auto:
            return
        with self._lock:
            if self.pending is None:
                self.pending = {"from": old, "since": time.time()}
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            pending, self.pending, self._timer = self.pending, None, None
        if pending is None:
            return
        old = pending["from"]
        if old == self.store.policy.version:
            return  # edited and reverted within the debounce: nothing changed
        coro = self.run("auto", prev_version=old)
        loop = self.loop
        try:
            if loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=600)
            else:
                asyncio.run(coro)
        except Exception:  # noqa: BLE001 - a failed self-test must never take the gateway down
            log.exception("automatic self-test failed")

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer, self.pending = None, None


def _key(sc_id: str, i: int) -> str:
    return f"{sc_id}#{i}"


def _regressions(prev: dict[str, Any], now: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Steps that passed in `prev` and fail now (known gaps excluded)."""
    before = {_key(sc["id"], i): s["passed"] for sc in prev.get("scenarios", []) for i, s in enumerate(sc["steps"])}
    out = []
    for sc in now:
        for i, s in enumerate(sc["steps"]):
            if not s["passed"] and not s["known_gap"] and before.get(_key(sc["id"], i)):
                out.append(
                    {
                        "scenario": sc["id"],
                        "title": sc["title"],
                        "step": i,
                        "why": s["why"],
                        "expect": s["expect"],
                        "actual": s["actual"],
                        "reason": s["reason"],
                    }
                )
    return out


def _note(old: str | None, new: str, regressions: list[dict[str, Any]]) -> str:
    n = len(regressions)
    tail = f"{n} regression{'' if n == 1 else 's'}"
    return f"policy {old} -> {new}: {tail}" if old else f"policy {new}: {tail}"


def pytest_report_path(store: PolicyStore) -> Path:
    env = os.environ.get("ACL_PYTEST_REPORT")
    return Path(env) if env else store.base_dir / "reports" / "acl-report.json"


def register(app: FastAPI, layer: Any, store: PolicyStore) -> SelfTest:
    selftest = SelfTest(layer, store)
    app.state.selftest = selftest

    @app.post("/admin/selftest/run")
    async def selftest_run():
        return await selftest.run("manual")

    @app.get("/admin/selftest/latest")
    async def selftest_latest():
        return {"status": selftest.status(), "result": selftest.latest()}

    @app.get("/admin/selftest/history")
    async def selftest_history():
        return {
            "runs": [
                {
                    k: r[k]
                    for k in ("run_id", "trigger", "policy_version", "prev_version", "started_at", "summary", "note")
                }
                for r in reversed(selftest.history)
            ]
        }

    @app.get("/admin/selftest/pytest")
    async def selftest_pytest():
        path = pytest_report_path(store)
        junit = path.parent / "junit.xml"
        try:
            report = json.loads(path.read_text())
        except (OSError, ValueError):
            report = None
        return {"report": report, "path": str(path), "junit": junit.exists()}

    @app.get("/admin/selftest/junit.xml")
    async def selftest_junit():
        junit = pytest_report_path(store).parent / "junit.xml"
        if not junit.exists():
            return JSONResponse({"error": "no JUnit report; run `make selftest`"}, status_code=404)
        return FileResponse(junit, media_type="application/xml", filename="junit.xml")

    return selftest
