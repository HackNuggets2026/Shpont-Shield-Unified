"""Reach: what one person or agent can touch, and whether they hold the lethal trifecta.

An AI agent becomes dangerous when one identity can (1) read private data, (2) take in content written by
outsiders, and (3) send something out of the company. Any one of these is ordinary; all three on the same
identity means a single planted instruction can move private data out. Each capability comes from a role's
tool patterns, an access grant, a resource entitlement or an agent grant, so removing one source can break
the combination. Ported from the Redteam reach map, reading the gateway's own policy and state.
"""

from __future__ import annotations

from fnmatch import fnmatch
from typing import Any

from . import resources
from .config import Policy
from .state import StateStore
from .types import Principal

LEGS = ("private", "untrusted", "egress")
LEG_VERB = {"private": "reads private data", "untrusted": "takes in outside content", "egress": "sends data out"}

# Concrete tool names the role patterns are resolved against, by the capability each one gives.
TOOLS = {
    "private": ["read_file"],
    "untrusted": ["read_file", "search_docs", "http_get", "get_weather"],
    "egress": ["http_get", "send_email"],
}
# Resources: private if sensitive or holding personal data; untrusted if outsiders write into them;
# egress through a scope that shares outside, or a write/exec scope on an outward-facing service.
PRIVATE_SENSITIVITY = {"high"}
PRIVATE_SCOPES = {"pii"}
UNTRUSTED_RESOURCES = {"zendesk", "notion", "slack", "github-acme", "google-drive"}
EGRESS_SCOPES = {"external_share"}
EGRESS_RESOURCES = {"slack": {"write"}, "zapier": {"exec"}, "sendgrid": {"write"}}


def _resource_legs(rid: str, sensitivity: str, scopes: list[str]) -> list[str]:
    legs = []
    if sensitivity in PRIVATE_SENSITIVITY or set(scopes) & PRIVATE_SCOPES:
        legs.append("private")
    if rid in UNTRUSTED_RESOURCES:
        legs.append("untrusted")
    if set(scopes) & EGRESS_SCOPES or set(scopes) & EGRESS_RESOURCES.get(rid, set()):
        legs.append("egress")
    return legs


def _sources(policy: Policy, state: StateStore, who: Principal) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    ta = policy.tool_access
    rbac = ta.enabled and ta.mode.value == "block" and not ta.shadow
    patterns = ta.roles.get(who.role, []) if rbac else ["*"]
    for leg, names in TOOLS.items():
        for name in names:
            # A tool behind an access-grant resource is decided by its grant, not the role.
            if policy.grant_resources(name):
                g = policy.covering_grant(who.id, name, None)
                if g:
                    out.append({"leg": leg, "kind": "tool", "id": name, "label": name, "via": f"grant:{g.resource}"})
                continue
            irreversible = rbac and any(fnmatch(name, p) for p in ta.irreversible)
            if any(fnmatch(name, p) for p in patterns) and not irreversible:
                out.append({"leg": leg, "kind": "tool", "id": name, "label": name, "via": f"role:{who.role}"})
    # Resources: a human's own entitlements; an agent only what its owner granted it.
    via = f"grant:{who.owner}" if who.kind == "agent" else "entitled"
    for rid, scopes in resources.usable(policy, state, who).items():
        r = policy.resources[rid]
        for leg in _resource_legs(rid, r.sensitivity, scopes):
            out.append({"leg": leg, "kind": "resource", "id": rid, "label": r.name, "via": via, "scopes": scopes})
    return out


def _affects(policy: Policy, src: dict[str, Any]) -> int:
    """How many identities hold the same source, so the fix that takes the least away can win."""
    keys = policy.identity.api_keys.values()
    if src["via"].startswith("role:"):
        return sum(1 for k in keys if k.role == src["via"][5:])
    if src["via"] == "entitled":
        r = policy.resources[src["id"]]
        return sum(1 for k in keys if k.kind == "human" and r.entitled.allows(k.principal, k.team, k.role))
    return 1


def _describe(policy: Policy, who: Principal, src: dict[str, Any]) -> str:
    via = src["via"]
    if src["kind"] == "tool" and via.startswith("role:"):
        return f"Remove {src['id']} from the {via[5:]} role"
    if src["kind"] == "tool":
        return f"Revoke {who.id}'s {via[6:]} access grant"
    if via.startswith("grant:"):
        return f"Revoke the {src['label']} grant {via[6:]} gave {who.id}"
    return f"Narrow the {src['label']} entitlement so it no longer covers {who.id}"


def reach(policy: Policy, state: StateStore, who: Principal) -> dict[str, Any]:
    srcs = _sources(policy, state, who)
    legs: dict[str, list[dict[str, Any]]] = {leg: [s for s in srcs if s["leg"] == leg] for leg in LEGS}
    trifecta = all(legs.values())
    weakest = fix = None
    if trifecta:
        singles = [legs[leg][0] for leg in LEGS if len(legs[leg]) == 1]
        if singles:
            # One source carries a whole leg: removing it breaks the combination. Prefer the one taking least away.
            src = min(singles, key=lambda s: _affects(policy, s))
            weakest, n = src["leg"], _affects(policy, src)
            fix = _describe(policy, who, src) + (f" (affects {n} identities)" if n > 1 else "") + "."
        else:
            weakest = min(LEGS, key=lambda leg: len(legs[leg]))
            group = legs[weakest]
            names = ", ".join(s["label"] for s in group)
            how = f"the {len(group)} ways it {LEG_VERB[weakest]}"
            fix = f"No single change breaks it; the fewest cuts are {how}: {names}."
    return {
        "principal": who.id,
        "kind": who.kind,
        "role": who.role,
        "owner": who.owner,
        "legs": legs,
        "trifecta": trifecta,
        "weakest": weakest,
        "fix": fix,
    }
