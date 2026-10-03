"""Company resources delegated by employees to their agents, brokered by the gateway."""

from __future__ import annotations

import time
from typing import Any

from .config import Policy, Resource
from .state import StateStore
from .types import Principal


class GrantError(ValueError):
    pass


def _human(policy: Policy, pid: str):
    return next((k for k in policy.identity.api_keys.values() if k.principal == pid and k.kind == "human"), None)


def suspended(policy: Policy, state: StateStore, rid: str) -> bool:
    r = policy.resources.get(rid)
    return r is None or r.suspended or rid in state.suspended


def entitled(policy: Policy, principal: Principal) -> dict[str, Resource]:
    """Resources a human may use and delegate (agents: those of their owner)."""
    who = _human(policy, principal.owner) if principal.kind == "agent" else _human(policy, principal.id)
    if who is None:
        return {}
    return {rid: r for rid, r in policy.resources.items() if r.entitled.allows(who.principal, who.team, who.role)}


def agents_of(policy: Policy, owner: str) -> list[dict[str, str]]:
    return [
        {"principal": k.principal, "team": k.team, "role": k.role}
        for k in policy.identity.api_keys.values()
        if k.kind == "agent" and k.owner == owner
    ]


def grant(
    policy: Policy, state: StateStore, owner: Principal, agent: str, rid: str, scopes: list[str], hours: float | None
) -> dict[str, Any]:
    if owner.kind != "human":
        raise GrantError("only employees can grant resources")
    if agent not in {a["principal"] for a in agents_of(policy, owner.id)}:
        raise GrantError(f"{agent!r} is not one of your agents")
    res = entitled(policy, owner).get(rid)
    if res is None:
        raise GrantError(f"you are not entitled to {rid!r}")
    if suspended(policy, state, rid):
        raise GrantError(f"{rid!r} is suspended by security")
    bad = set(scopes) - set(res.scopes)
    if not scopes or bad:
        raise GrantError(f"scopes must be a non-empty subset of {res.scopes}")
    if res.max_grant_hours is not None and (hours is None or hours > res.max_grant_hours):
        raise GrantError(f"{rid!r} can be granted for at most {res.max_grant_hours} hours")
    now = time.time()
    g = {
        "scopes": sorted(set(scopes)),
        "granted_by": owner.id,
        "granted_at": now,
        "expires_at": now + hours * 3600 if hours else None,
    }
    state.grants.setdefault(agent, {})[rid] = g
    state.save()
    return g


def revoke(state: StateStore, agent: str, rid: str) -> bool:
    removed = state.grants.get(agent, {}).pop(rid, None) is not None
    if removed:
        state.save()
    return removed


def active_grant(policy: Policy, state: StateStore, agent: Principal, rid: str) -> dict[str, Any] | None:
    """The grant as it stands right now: expiry, suspension and the owner's entitlement are re-checked
    on every use, so losing a role or a suspension takes effect immediately."""
    g = state.grants.get(agent.id, {}).get(rid)
    if g is None or suspended(policy, state, rid) or rid not in entitled(policy, agent):
        return None
    if g["expires_at"] is not None and g["expires_at"] < time.time():
        return None
    return g


def usable(policy: Policy, state: StateStore, principal: Principal) -> dict[str, list[str]]:
    """resource id -> scopes this principal can use now (humans: entitlement; agents: grants)."""
    if principal.kind == "agent":
        out = {}
        for rid in state.grants.get(principal.id, {}):
            g = active_grant(policy, state, principal, rid)
            if g:
                out[rid] = g["scopes"]
        return out
    return {rid: r.scopes for rid, r in entitled(policy, principal).items() if not suspended(policy, state, rid)}
