"""Thin client for apps and agents that call the gateway's guard API directly.

For model traffic no SDK is needed: point any OpenAI-compatible client at
`http://<gateway>/v1` with your control-layer API key. For MCP, point the agent at
`http://<gateway>/mcp/<server>`.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx


class PolicyViolation(Exception):
    def __init__(self, verdict: Verdict):
        super().__init__(f"{verdict.action}: {verdict.reason}")
        self.verdict = verdict


@dataclass
class Verdict:
    action: str
    text: str
    reason: str
    request_id: str
    findings: list[dict[str, Any]]

    @property
    def allowed(self) -> bool:
        return self.action != "block"


class Guard:
    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0):
        self.http = httpx.Client(base_url=base_url, headers={"x-api-key": api_key}, timeout=timeout)

    def check(self, text: str, direction: str = "input", **extra: Any) -> Verdict:
        r = self.http.post("/v1/guard", json={"text": text, "direction": direction, **extra})
        body = r.json()
        if "action" not in body:
            raise RuntimeError(f"guard API error {r.status_code}: {body}")
        return Verdict(body["action"], body["text"], body["reason"], body["request_id"], body["findings"])

    def enforce(self, text: str, direction: str = "input", **extra: Any) -> str:
        """Return the (possibly redacted) text, or raise PolicyViolation if blocked."""
        v = self.check(text, direction, **extra)
        if not v.allowed:
            raise PolicyViolation(v)
        return v.text

    def tool(self, fn: Callable[..., str]) -> Callable[..., str]:
        """Wrap a local tool: arguments are checked as a tool_call, the result as a tool_result."""

        @functools.wraps(fn)
        def wrapper(**kwargs: Any) -> str:
            self.enforce(json.dumps(kwargs), "tool_call", tool=fn.__name__)
            return self.enforce(str(fn(**kwargs)), "tool_result", tool=fn.__name__)

        return wrapper
