"""Identity, model allowlist and tool authorization."""

from __future__ import annotations

import re
from fnmatch import fnmatch

from .. import services
from ..config import Policy
from ..resources import usable
from ..state import StateStore
from ..types import Action, Context, Direction, Finding, Principal
from .base import finding

ANONYMOUS = Principal(id="anonymous", team="none", role="none", authenticated=False)


def authenticate(policy: Policy, api_key: str | None) -> Principal:
    entry = policy.identity.api_keys.get(api_key or "")
    if entry is None:
        return ANONYMOUS
    return Principal(id=entry.principal, team=entry.team, role=entry.role, kind=entry.kind, owner=entry.owner)


def by_principal(policy: Policy, pid: str | None) -> Principal:
    key = next((k for k, v in policy.identity.api_keys.items() if v.principal == pid), None)
    return authenticate(policy, key)


def identify(policy: Policy, api_key: str | None) -> Principal:
    """A gateway caller: its API key's principal, else (demo mode only) the demo principal."""
    p = authenticate(policy, api_key)
    if not p.authenticated and policy.identity.demo_mode:
        return by_principal(policy, policy.identity.demo_principal)
    return p


def check_auth(ctx: Context, policy: Policy) -> list[Finding]:
    if policy.identity.require_auth and not ctx.principal.authenticated:
        return [_hard("auth", "unauthenticated", "missing or unknown API key")]
    pp = policy.principal(ctx.principal.id)
    if pp.status == "revoked":
        return [_hard("auth", "revoked", f"access revoked: {pp.reason or 'by an administrator'}")]
    return []


def check_model(ctx: Context, policy: Policy) -> list[Finding]:
    allowed = policy.models.allowed
    if ctx.direction is not Direction.INPUT or not allowed:
        return []
    if not ctx.model and ctx.channel in ("chat", "messages"):
        return [_hard("model_allowlist", "model_missing", "request names no model")]
    if ctx.model:
        if not any(fnmatch(ctx.model, pat) for pat in allowed):
            return [_hard("model_allowlist", "model_not_allowed", f"model {ctx.model!r} not in allowlist")]
    return []


def check_resource(ctx: Context, policy: Policy, state: StateStore) -> list[Finding]:
    """Brokered resources: agents need a live grant from their owner, humans an entitlement."""
    if ctx.resource is None:
        return []
    scopes = usable(policy, state, ctx.principal).get(ctx.resource)
    if scopes is None:
        who = "granted to this agent" if ctx.principal.kind == "agent" else "available to you"
        return [_hard("resource_access", "not_granted", f"{ctx.resource!r} is not {who}")]
    if ctx.scope and ctx.scope not in scopes:
        return [_hard("resource_access", "scope_denied", f"{ctx.resource!r}: scope {ctx.scope!r} not in {scopes}")]
    tool = services.TOOLS.get(ctx.tool or "") if ctx.scope and ctx.tool_args is not None else None
    outside = [a for a in services.recipients(tool, ctx.tool_args or {}) if not _inside(a, policy)] if tool else []
    if outside and services.EXTERNAL_SHARE not in scopes:
        return [
            _hard(
                "resource_access",
                "external_recipient",
                f"{outside} is outside the company domains {policy.company_domains}; that needs external_share",
            )
        ]
    return []


_ADDRESS = re.compile(r"[^@\s,;<>\"]+@([a-z0-9-]+(?:\.[a-z0-9-]+)+)")


def _inside(address: str, policy: Policy) -> bool:
    """One plain address in a company domain or its subdomain. Anything else (a list, a display
    name, a typo) counts as outside."""
    m = _ADDRESS.fullmatch(address.lower())
    return bool(m) and any(m[1] == d or m[1].endswith("." + d) for d in map(str.lower, policy.company_domains))


def check_tool(ctx: Context, policy: Policy) -> list[Finding]:
    cfg = policy.tool_access
    if not cfg.enabled or ctx.direction is not Direction.TOOL_CALL or not ctx.tool:
        return []
    # A tool behind an access-grant resource is decided by its grant (controls/resources.py), not the role.
    protected = bool(policy.grant_resources(ctx.tool))
    grant = policy.covering_grant(ctx.principal.id, ctx.tool, ctx.workflow) if protected else None
    allowed = cfg.roles.get(ctx.principal.role, [])
    # A broker call (resource + scope) is authorised by its grant instead of role-based tool permission.
    # Reaching a catalogued MCP server (resource, no scope) does not lift the per-tool rules.
    brokered = ctx.resource is not None and ctx.scope is not None
    if not protected and not brokered and not any(fnmatch(ctx.tool, pat) for pat in allowed):
        return [
            finding(
                "tool_access",
                cfg,
                "tool_not_allowed",
                Action.BLOCK,
                detail=f"role {ctx.principal.role!r} may not call {ctx.tool!r}",
            )
        ]
    if policy.principal(ctx.principal.id).status == "quarantined" and not any(
        fnmatch(ctx.tool, pat) for pat in policy.quarantine.tools
    ):
        return [
            finding(
                "tool_access",
                cfg,
                "quarantined",
                Action.BLOCK,
                detail=f"{ctx.principal.id!r} is quarantined; only {policy.quarantine.tools} are allowed",
            )
        ]
    if grant is None and any(fnmatch(ctx.tool, pat) for pat in cfg.irreversible):
        how = "an approved grant" if protected else "human approval"
        return [
            finding(
                "tool_access",
                cfg,
                "irreversible_action",
                Action.BLOCK,
                detail=f"{ctx.tool!r} is irreversible and needs {how}",
            )
        ]
    return []


def _hard(control: str, category: str, detail: str) -> Finding:
    """Gate findings (auth, model allowlist) are not mode-capped: they always block."""
    return Finding(control=control, category=category, action=Action.BLOCK, proposed=Action.BLOCK, detail=detail)
