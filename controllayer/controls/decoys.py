"""Traps: decoy documents, datasets and credentials that serve no purpose, so touching one is a signal.

The check never changes what happens to the request (its findings are `allow`): the person or agent carries on
and sees nothing unusual, while the risk engine opens an incident. What counts, per direction:

- a tool call naming a decoy's identifier: it was **opened**;
- its marker in a prompt or a tool call: its content was **leaked** onward;
- a prompt naming it (identifier or phrase): it was **mentioned**, a lighter signal.

Tool results, tool descriptions and model output never count, so listing or searching past a decoy is safe.
Neither do re-checks of conversation history (unmetered prompts): only the new message speaks for the person.
"""

from __future__ import annotations

import re

from ..config import Decoy, Policy
from ..types import Action, Context, Direction, Finding

CONTROL = "decoys"
_RANK = {"mentioned": 0, "opened": 1, "leaked": 2}


def _norm(text: str) -> str:
    """Lowercase words separated by single spaces, padded, so `Board-Pack_Q3.pdf` matches `board pack q3 pdf`."""
    return f" {re.sub(r'[^a-z0-9]+', ' ', text.lower()).strip()} "


def _has(text: str, needle: str) -> bool:
    n = _norm(needle).strip()
    return bool(n) and f" {n} " in text


def touch(ctx: Context, d: Decoy, text: str) -> str | None:
    """How this traffic touches the decoy, or None. `text` is `_norm(ctx.text)`."""
    found: list[str] = []
    if ctx.direction in (Direction.INPUT, Direction.TOOL_CALL) and _has(text, d.marker):
        found.append("leaked")
    if ctx.direction is Direction.TOOL_CALL and any(_has(text, i) for i in d.identifiers):
        found.append("opened")
    if ctx.direction is Direction.INPUT and any(_has(text, x) for x in [*d.identifiers, *d.phrases]):
        found.append("mentioned")
    return max(found, key=_RANK.__getitem__) if found else None


def check(ctx: Context, policy: Policy) -> list[Finding]:
    if not policy.decoys or ctx.direction not in (Direction.INPUT, Direction.TOOL_CALL):
        return []
    if ctx.direction is Direction.INPUT and not ctx.metered:
        return []  # history and whole-body re-checks: a search result listing a trap is not asking for it
    text = _norm(ctx.text)
    out = []
    for name, d in policy.decoys.items():
        how = touch(ctx, d, text)
        if how is None:
            continue
        via = f" with {ctx.tool}" if ctx.tool else ""
        detail = {
            "opened": f"opened the decoy {d.title!r}{via}",
            "leaked": f"moved content of the decoy {d.title!r} on{via}",
            "mentioned": f"asked for the decoy {d.title!r} by name",
        }[how]
        out.append(Finding(control=CONTROL, category=f"{name}:{how}", action=Action.ALLOW, proposed=Action.ALLOW,
                           detail=detail))  # fmt: skip
    return out


def opened(findings: list[Finding], policy: Policy) -> Decoy | None:
    """The decoy a tool call opened, when the gateway should answer it with the decoy's content."""
    for f in findings:
        if f.control == CONTROL and f.category.endswith(":opened"):
            d = policy.decoys.get(f.category.rpartition(":")[0])
            if d and d.serve and d.content:
                return d
    return None


def is_decoy_finding(f: Finding | dict) -> bool:
    control = f.control if isinstance(f, Finding) else f.get("control")
    return control == CONTROL
