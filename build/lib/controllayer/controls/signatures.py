"""Known-attack signatures, loaded from an externally managed feed (file path or URL)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..config import SignatureControl
from ..types import Action, Context, Direction, Finding, Span
from .base import applies, finding


@dataclass
class Signature:
    id: str
    name: str
    category: str
    pattern: re.Pattern[str]
    action: Action
    severity: str
    reference: str
    directions: list[Direction]


@dataclass
class SignatureFeed:
    location: str
    signatures: list[Signature] = field(default_factory=list)
    feed_version: str = "none"
    loaded_at: float = 0.0
    errors: list[str] = field(default_factory=list)

    def load(self, base_dir: Path) -> None:
        if self.location.startswith(("http://", "https://")):
            raw = httpx.get(self.location, timeout=5).text
        else:
            raw = (base_dir / self.location).read_text()
        self._apply(json.loads(raw))

    async def aload(self, base_dir: Path) -> None:
        if self.location.startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=5) as c:
                self._apply((await c.get(self.location)).json())
        else:
            self.load(base_dir)

    def _apply(self, data: dict) -> None:
        sigs, errors = [], []
        for entry in data.get("signatures", []):
            try:
                flags = re.IGNORECASE if "i" in entry.get("flags", "") else 0
                sigs.append(
                    Signature(
                        id=entry["id"],
                        name=entry["name"],
                        category=entry.get("category", "exploit"),
                        pattern=re.compile(entry["pattern"], flags),
                        action=Action(entry.get("action", "block")),
                        severity=entry.get("severity", "high"),
                        reference=entry.get("reference", ""),
                        directions=[Direction(d) for d in entry.get("directions", list(Direction))],
                    )
                )
            except (KeyError, ValueError, re.error) as e:
                errors.append(f"{entry.get('id', '?')}: {e}")
        self.signatures = sigs
        self.errors = errors
        self.feed_version = str(data.get("feed_version", "unknown"))
        self.loaded_at = time.time()


def check(ctx: Context, cfg: SignatureControl, feed: SignatureFeed) -> list[Finding]:
    if not applies(cfg, ctx):
        return []
    out = []
    for sig in feed.signatures:
        if ctx.direction not in sig.directions:
            continue
        m = sig.pattern.search(ctx.text)
        if m:
            out.append(
                finding(
                    "signatures",
                    cfg,
                    sig.category,
                    sig.action,
                    detail=f"{sig.id} {sig.name} ({sig.reference})".strip(),
                    spans=[Span(m.start(), m.end(), sig.id)],
                )
            )
    return out
