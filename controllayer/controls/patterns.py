"""Deterministic PII and secret detectors."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from ..config import PatternControl
from ..types import Action, Context, Finding, Span
from .base import applies, finding


def _luhn(s: str) -> bool:
    digits = [int(c) for c in s if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _iban(s: str) -> bool:
    s = s.replace(" ", "").upper()
    rearranged = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1


def _pesel(s: str) -> bool:
    w = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
    return (10 - sum(int(a) * b for a, b in zip(s[:10], w)) % 10) % 10 == int(s[10])


@dataclass(frozen=True)
class Detector:
    pattern: re.Pattern[str]
    validate: Callable[[str], bool] | None = None


PII: dict[str, Detector] = {
    "email": Detector(re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b")),
    "credit_card": Detector(re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), _luhn),
    "iban": Detector(re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b"), _iban),
    "pesel": Detector(re.compile(r"\b\d{11}\b"), _pesel),
    "us_ssn": Detector(re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
    "phone": Detector(re.compile(r"(?<![\w+])\+?\d{1,3}[ -]?\(?\d{2,3}\)?[ -]?\d{3}[ -]?\d{3,4}\b")),
    "ipv4": Detector(re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
}

SECRETS: dict[str, Detector] = {
    "aws_access_key": Detector(re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    "github_token": Detector(re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    "openai_key": Detector(re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    "slack_token": Detector(re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    "private_key": Detector(re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    "jwt": Detector(re.compile(r"\beyJ[\w-]{8,}\.eyJ[\w-]{8,}\.[\w-]{8,}\b")),
    "password_assignment": Detector(
        re.compile(r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key)\s*[:=]\s*['\"]?[^\s'\"]{6,}")
    ),
    "credentials_in_url": Detector(re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@/]+@[^\s]+")),
}


class PatternDetector:
    def __init__(self, name: str, detectors: dict[str, Detector]):
        self.name = name
        self.detectors = detectors

    def check(self, ctx: Context, cfg: PatternControl) -> list[Finding]:
        if not applies(cfg, ctx):
            return []
        out = []
        claimed: list[Span] = []
        # Detector order is priority order: a phone-shaped run of digits inside a card number is the card.
        for entity, det in self.detectors.items():
            proposed = cfg.entities.get(entity)
            if proposed is None:
                continue
            spans = [
                Span(m.start(), m.end(), entity)
                for m in det.pattern.finditer(ctx.text)
                if (det.validate is None or det.validate(m.group()))
                and not any(m.start() < c.end and c.start < m.end() for c in claimed)
            ]
            claimed += spans
            if spans:
                out.append(
                    finding(
                        self.name,
                        cfg,
                        entity,
                        proposed,
                        detail=f"{len(spans)} x {entity}",
                        spans=spans,
                    )
                )
        return out


pii = PatternDetector("pii", PII)
secrets = PatternDetector("secrets", SECRETS)


def redact(text: str, spans: list[Span]) -> str:
    """Replace spans right-to-left; overlapping spans are merged."""
    merged: list[Span] = []
    for s in sorted(spans, key=lambda s: s.start):
        if merged and s.start < merged[-1].end:
            last = merged[-1]
            merged[-1] = Span(last.start, max(last.end, s.end), last.label)
        else:
            merged.append(s)
    for s in reversed(merged):
        text = text[: s.start] + f"[REDACTED:{s.label}]" + text[s.end :]
    return text
