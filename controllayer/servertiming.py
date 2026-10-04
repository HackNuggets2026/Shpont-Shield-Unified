"""Per-request timing and decision, sent back as `Server-Timing` and `X-Shield-*` response headers.

The gateway middleware opens a record for each model or MCP request; the engine notes every verdict
and the upstream clients time their calls. `guard` is everything the gateway added on top of the upstream
call (inspection, policy, audit), so guard + upstream = total.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable
from contextvars import ContextVar
from typing import Any, TypeVar

from .types import Action

T = TypeVar("T")

PATHS = ("/v1/chat/completions", "/v1/messages", "/mcp/")

_current: ContextVar[dict[str, Any] | None] = ContextVar("shield_timing", default=None)


def applies(path: str) -> bool:
    return path == "/v1/messages" or path.startswith(PATHS)


def start() -> dict[str, Any]:
    rec: dict[str, Any] = {"t0": time.perf_counter(), "upstream": None, "request_id": None, "action": None}
    _current.set(rec)
    return rec


def note_verdict(verdict: Any) -> None:
    rec = _current.get()
    if rec is None:
        return
    rec["request_id"] = rec["request_id"] or verdict.request_id
    if rec["action"] is None or verdict.action.rank > rec["action"].rank:
        rec["action"] = verdict.action


async def timed(call: Awaitable[T]) -> T:
    """Await an upstream call, adding its duration to the request's `upstream` time."""
    t0 = time.perf_counter()
    try:
        return await call
    finally:
        rec = _current.get()
        if rec is not None:
            rec["upstream"] = (rec["upstream"] or 0.0) + (time.perf_counter() - t0) * 1000


def headers(rec: dict[str, Any], policy_version: str, status: int, existing: Any) -> dict[str, str]:
    total = (time.perf_counter() - rec["t0"]) * 1000
    upstream = rec["upstream"]
    guard = total - (upstream or 0.0)
    timing = f"guard;dur={guard:.1f}" + (f", upstream;dur={upstream:.1f}" if upstream is not None else "")
    action: Action | None = rec["action"]
    decision = action.value if action else ("allow" if status < 400 else "error")
    request_id = existing.get("x-control-request-id") or rec["request_id"] or uuid.uuid4().hex[:16]
    return {
        "Server-Timing": f"{timing}, total;dur={total:.1f}",
        "X-Shield-Request-Id": request_id,
        "X-Shield-Decision": decision,
        "X-Shield-Policy-Version": policy_version,
    }
