"""Identity, model allowlist and tool authorization."""

from __future__ import annotations

from fnmatch import fnmatch

from ..config import Policy
from ..types import Action, Context, Direction, Finding, Principal
from .base import finding

ANONYMOUS = Principal(id="anonymous", team="none", role="none", authenticated=False)


def authenticate(policy: Policy, api_key: str | None) -> Principal:
    entry = policy.identity.api_keys.get(api_key or "")
    if entry is None:
        return ANONYMOUS
    return Principal(id=entry.principal, team=entry.team, role=entry.role)


def check_auth(ctx: Context, policy: Policy) -> list[Finding]:
    if policy.identity.require_auth and not ctx.principal.authenticated:
        return [_hard("auth", "unauthenticated", "missing or unknown API key")]
    return []


def check_model(ctx: Context, policy: Policy) -> list[Finding]:
    allowed = policy.models.allowed
    if ctx.direction is not Direction.INPUT or not allowed:
        return []
    if not ctx.model and ctx.channel == "chat":
        return [_hard("model_allowlist", "model_missing", "request names no model")]
    if ctx.model:
        if not any(fnmatch(ctx.model, pat) for pat in allowed):
            return [_hard("model_allowlist", "model_not_allowed", f"model {ctx.model!r} not in allowlist")]
    return []


def check_tool(ctx: Context, policy: Policy) -> list[Finding]:
    cfg = policy.tool_access
    if not cfg.enabled or ctx.direction is not Direction.TOOL_CALL or not ctx.tool:
        return []
    allowed = cfg.roles.get(ctx.principal.role, [])
    if not any(fnmatch(ctx.tool, pat) for pat in allowed):
        return [
            finding(
                "tool_access",
                cfg,
                "tool_not_allowed",
                Action.BLOCK,
                detail=f"role {ctx.principal.role!r} may not call {ctx.tool!r}",
            )
        ]
    if any(fnmatch(ctx.tool, pat) for pat in cfg.irreversible):
        return [
            finding(
                "tool_access",
                cfg,
                "irreversible_action",
                Action.BLOCK,
                detail=f"{ctx.tool!r} is irreversible and needs human approval",
            )
        ]
    return []


def _hard(control: str, category: str, detail: str) -> Finding:
    """Gate findings (auth, model allowlist) are not mode-capped: they always block."""
    return Finding(control=control, category=category, action=Action.BLOCK, proposed=Action.BLOCK, detail=detail)
