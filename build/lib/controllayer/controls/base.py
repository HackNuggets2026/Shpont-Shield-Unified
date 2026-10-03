from __future__ import annotations

from ..config import ControlBase
from ..types import Action, Context, Finding, Span


def finding(
    control: str,
    cfg: ControlBase,
    category: str,
    proposed: Action,
    *,
    score: float = 1.0,
    detail: str = "",
    spans: list[Span] | None = None,
    tier: str = "deterministic",
) -> Finding:
    """Build a finding, applying the control's mode cap and shadow flag."""
    action = Action.ALLOW if cfg.shadow else proposed.cap(cfg.mode)
    return Finding(
        control=control,
        category=category,
        action=action,
        proposed=proposed,
        score=score,
        detail=detail,
        spans=spans or [],
        tier=tier,
        shadow=cfg.shadow,
    )


def applies(cfg: ControlBase, ctx: Context) -> bool:
    return cfg.enabled and ctx.direction in cfg.directions
