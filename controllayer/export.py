"""Audit decisions, admin notes and insider-risk alerts as OCSF 1.9.0 or ECS 9.5 documents.

Both are views over the native records: they carry the same text (masked spans, or none for a
block) and add `raw_text` only where the native record has it, so the privacy rules of
`audit.store_raw_text` and watch capture hold in every format.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
from datetime import UTC, datetime
from typing import Any, Literal

OCSF_VERSION = "1.9.0"
ECS_VERSION = "9.5.0"
PRODUCT = {"name": "AI Control Layer", "uid": "controllayer", "vendor_name": "Shpont Shield"}
_SERVICE = {"name": PRODUCT["name"], "uid": PRODUCT["uid"]}  # the gateway answers every request itself

Record = Literal["decision", "note", "alert"]
Format = Literal["native", "ocsf", "ecs"]

# action -> (OCSF action_id, disposition_id, severity_id)
_ACTION = {
    "allow": (1, 1, 1),  # Allowed / Allowed / Informational
    "log": (3, 17, 1),  # Observed / Logged / Informational
    "warn": (1, 19, 2),  # Allowed / Alert / Low
    "redact": (4, 11, 3),  # Modified / Corrected / Medium
    "block": (2, 2, 4),  # Denied / Blocked / High
}
_DISPOSITION = {1: "Allowed", 2: "Blocked", 11: "Corrected", 17: "Logged", 19: "Alert"}
_ACTION_NAME = {1: "Allowed", 2: "Denied", 3: "Observed", 4: "Modified"}
_SEVERITY = {1: "Informational", 2: "Low", 3: "Medium", 4: "High"}
# insider-risk level -> OCSF risk_level_id (and alert severity_id)
_RISK = {"normal": 1, "watch": 2, "restricted": 3}
_RISK_NAME = {1: "Low", 2: "Medium", 3: "High"}
_RISK_SEVERITY = {"normal": 2, "watch": 3, "restricted": 4}
# Content flowing into the model is the prompt; what it produced (replies, tool calls) the response.
_TO_MODEL = {"input", "tool_result", "tool_description"}
# message_context.ai_role_id: User, Assistant, Agent, Tool
_AI_ROLE = {"input": 1, "output": 2, "tool_call": 4, "tool_result": 3, "tool_description": 3}
_AI_ROLE_NAME = {1: "User", 2: "Assistant", 3: "Tool", 4: "Agent"}


def convert(record: dict[str, Any], kind: Record, fmt: Format) -> dict[str, Any]:
    if fmt == "native":
        return record
    return (_OCSF if fmt == "ocsf" else _ECS)[kind](record)


def event_uid(record: dict[str, Any]) -> str:
    """Stable per record, so a SIEM can de-duplicate repeated exports."""
    return hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()[:32]


def _ms(ts: float) -> int:
    return int(ts * 1000)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ip(record: dict[str, Any]) -> dict[str, str]:
    """`{"ip": ...}` when the peer address is an IP (OCSF ip_t / ECS ip reject anything else)."""
    try:
        return {"ip": str(ipaddress.ip_address(record.get("src_ip") or ""))}
    except ValueError:
        return {}


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None and v != {} and v != []}


# ---- OCSF ----------------------------------------------------------------------------------


def _user(uid: str | None, team: str | None = None, role: str | None = None, agent: bool = False) -> dict:
    groups = [g for g in ({"name": team, "type": "team"}, {"name": role, "type": "role"}) if g["name"]]
    if agent:
        kind = {"type_id": 99, "type": "AI Agent"}
    elif uid == "security":
        kind = {"type_id": 2, "type": "Admin"}
    else:
        kind = {"type_id": 1, "type": "User"}
    return _drop_none({"uid": uid, "name": uid, **kind, "groups": groups})


def _agent(pid: str, model: dict | None = None) -> dict:
    return _drop_none({"uid": pid, "name": pid, "type_id": 0, "type": "Unknown", "ai_model": model})


def _delegation(agent: str, owner: str) -> dict:
    # The agent's key in policy.yaml names its owner: that binding is the delegated authority.
    return {"uid": f"{owner}/{agent}", "issuer_uid": PRODUCT["uid"]}


def _base(record: dict, class_uid: int, class_name: str, category: tuple[int, str], activity: tuple[int, str]) -> dict:
    return {
        "class_uid": class_uid,
        "class_name": class_name,
        "category_uid": category[0],
        "category_name": category[1],
        "activity_id": activity[0],
        "activity_name": activity[1],
        "type_uid": class_uid * 100 + activity[0],
        "type_name": f"{class_name}: {activity[1]}",
        "time": _ms(record["ts"]),
    }


def _metadata(record: dict, log_name: str, profiles: list[str], correlation: str | None = None) -> dict:
    return _drop_none(
        {
            "version": OCSF_VERSION,
            "product": PRODUCT,
            "uid": event_uid(record),
            "correlation_uid": correlation,
            "log_name": log_name,
            "profiles": profiles,
        }
    )


def _controls(action: str | None) -> dict:
    if action not in _ACTION:
        return {}
    action_id, disposition_id, _ = _ACTION[action]
    return {
        "action_id": action_id,
        "action": _ACTION_NAME[action_id],
        "disposition_id": disposition_id,
        "disposition": _DISPOSITION[disposition_id],
    }


def _risk(level: str | None) -> dict:
    if level not in _RISK:
        return {}
    return {"risk_level_id": _RISK[level], "risk_level": _RISK_NAME[_RISK[level]]}


def ocsf_decision(e: dict[str, Any]) -> dict[str, Any]:
    """A gateway decision: API Activity (6003) with the security_control and ai_operation profiles."""
    direction = e["direction"]
    owner = e.get("owner")
    model = {"name": e["model"], "ai_provider": e.get("provider") or "unknown"} if e.get("model") else None
    _, _, severity_id = _ACTION[e["action"]]
    to_model = direction in _TO_MODEL
    text_key = "prompt_text" if to_model else "response_text"
    sent = direction in ("input", "tool_call")  # the caller's request; the rest comes back to it
    out = _base(e, 6003, "API Activity", (6, "Application Activity"), (1, "Create") if sent else (2, "Read"))
    out |= {
        "severity_id": severity_id,
        "severity": _SEVERITY[severity_id],
        "status_id": 1 if e["status_code"] < 400 else 2,
        "status": "Success" if e["status_code"] < 400 else "Failure",
        "status_code": str(e["status_code"]),
        "message": e["reason"] or e["action"],
        "is_alert": e["action"] in ("warn", "redact", "block"),
        **_controls(e["action"]),
        "metadata": _metadata(e, "audit", ["security_control", "ai_operation"], e["request_id"]),
        "api": {
            "operation": f"{e['channel']}/{direction}",
            "request": {"uid": e["request_id"]},
            "service": _SERVICE,
        },
        # An agent acts in its owner's user context; the agent itself is ai_agent.
        "actor": {"user": _user(owner, None, None) if owner else _user(e["principal"], e["team"], e["role"])},
        "src_endpoint": _ip(e),
        "policy": {"uid": e["policy_version"], "version": e["policy_version"]},
        "message_context": _drop_none(
            {
                "uid": e["request_id"],
                "service": _SERVICE,
                "ai_role_id": _AI_ROLE[direction],
                "ai_role": _AI_ROLE_NAME[_AI_ROLE[direction]],
                text_key: e.get("text"),
                "total_tokens": e.get("tokens"),
            }
        ),
        "unmapped": _drop_none(
            {
                "principal": e["principal"],
                "team": e["team"],
                "role": e["role"],
                "channel": e["channel"],
                "direction": direction,
                "tool": e.get("tool"),
                "text_sha256": e.get("text_sha256"),
                "raw_text": e.get("raw_text"),
                "findings": e["findings"],
                "latency_ms": e.get("latency_ms"),
                "usd": e.get("usd"),
            }
        ),
    }
    if owner:
        out["ai_agent"] = _agent(e["principal"], model)
        out["delegation"] = _delegation(e["principal"], owner)
    elif model:
        out["ai_model"] = model
    if e.get("tool"):
        out["resources"] = [{"uid": e["tool"], "name": e["tool"], "type": "tool"}]
    return _drop_none(out)


def ocsf_alert(a: dict[str, Any]) -> dict[str, Any]:
    """An insider-risk alert: Detection Finding (2004)."""
    owner = a.get("owner")
    severity_id = _RISK_SEVERITY[a["level"]]
    evidence = _drop_none(
        {
            "actor": {"user": _user(owner) if owner else _user(a["principal"], a.get("team"))},
            "ai_agent": _agent(a["principal"]) if owner else None,
            "api": {"operation": f"{a['channel']}/{a['direction']}", "request": {"uid": a["request_id"]}}
            if a.get("request_id")
            else None,
        }
    )
    out = _base(a, 2004, "Detection Finding", (2, "Findings"), (1, "Create"))
    out |= {
        "severity_id": severity_id,
        "severity": _SEVERITY[severity_id],
        "status_id": 1,
        "status": "New",
        "is_alert": True,
        "message": a["reason"],
        "risk_score": round(a["score"]),
        **_risk(a["level"]),
        **_controls(a.get("action")),
        "metadata": _metadata(a, "alerts", ["security_control"], a.get("request_id")),
        "finding_info": _drop_none(
            {
                "uid": event_uid(a),
                "title": f"Insider risk: {a['principal']} at {a['level']}",
                "desc": a["reason"],
                "types": ["Insider Risk"],
                "created_time": _ms(a["ts"]),
                "analytic": {"name": "insider_risk", "type_id": 2, "type": "Behavioral"},
                "related_events": [{"uid": a["request_id"]}] if a.get("request_id") else None,
            }
        ),
        "evidences": [evidence],
        "unmapped": _drop_none(
            {
                "principal": a["principal"],
                "owner": owner,
                "team": a.get("team"),
                "level": a["level"],
                "score": a["score"],
                "tool": a.get("tool"),
                "findings": a.get("findings"),
            }
        ),
    }
    return _drop_none(out)


def ocsf_note(n: dict[str, Any]) -> dict[str, Any]:
    """Administrative actions: grants as User Management (3007), the rest as Entity Management (3004)."""
    kind = n["kind"]
    details = {k: v for k, v in n.items() if k not in ("ts", "kind", "actor", "src_ip")}
    out = {
        "severity_id": 1,
        "severity": "Informational",
        "status_id": 1,
        "status": "Success",
        "message": kind,
        # A signal's actor is the integration (a system); everything else is a person or "security".
        "actor": {"application": {"name": n["integration"]}} if n.get("integration") else {"user": _user(n["actor"])},
        "src_endpoint": _ip(n),
        **_risk(n.get("level")),
        "metadata": _metadata(n, "audit", ["security_control"] if n.get("level") in _RISK else []),
    }
    if kind in ("grant", "revoke", "grant_scopes"):
        if kind != "grant_scopes":
            activity, privileges = _ACCESS[kind], (n.get("grant") or {}).get("scopes")
        elif n["added"] and not n["removed"]:
            activity, privileges = _ACCESS["grant"], n["added"]
        elif n["removed"] and not n["added"]:
            activity, privileges = _ACCESS["revoke"], n["removed"]
        else:
            activity, privileges = (2, "Update"), n["scopes"]
        out |= _base(n, 3007, "User Management", (3, "Identity & Access Management"), activity) | {
            "user": _user(n["agent"], agent=True),
            "privileges": privileges,
            "resources": [{"uid": n["resource"], "name": n["resource"], "type": "company_resource"}],
            "unmapped": details,
        }
        return _drop_none(out)
    if kind == "suspend":
        activity = (12, "Suspend") if n["suspended"] else (13, "Resume")
        entity = {"uid": n["resource"], "name": n["resource"], "type_id": 99, "type": "company_resource"}
    else:  # insider-risk changes about a principal
        activity = (3, "Update")
        entity = {"uid": n["principal"], "name": n["principal"], "type_id": 2, "type": "User"}
    out |= _base(n, 3004, "Entity Management", (3, "Identity & Access Management"), activity)
    out["entity"] = entity | {"data": details}
    return _drop_none(out)


_ACCESS = {"grant": (14, "Assign Privileges"), "revoke": (15, "Remove Privileges")}

_OCSF = {"decision": ocsf_decision, "alert": ocsf_alert, "note": ocsf_note}


# ---- ECS -----------------------------------------------------------------------------------


def _ecs_base(record: dict, kind: str, category: list[str], types: list[str], action: str, dataset: str) -> dict:
    return {
        "@timestamp": _iso(record["ts"]),
        "ecs": {"version": ECS_VERSION},
        "event": {
            "id": event_uid(record),
            "kind": kind,
            "category": category,
            "type": types,
            "action": action,
            "dataset": dataset,
            "provider": PRODUCT["uid"],
        },
        "observer": {"product": PRODUCT["name"], "vendor": PRODUCT["vendor_name"], "type": "gateway"},
    }


def _ecs_person(principal: str, owner: str | None, team: str | None = None, role: str | None = None) -> dict:
    """The person is `user`; an agent acting for them is `gen_ai.agent`."""
    out: dict[str, Any] = {
        "user": _drop_none({"id": owner or principal, "name": owner or principal}),
        "related": {"user": [principal, owner] if owner else [principal]},
    }
    if not owner:
        out["user"] |= _drop_none({"roles": [role] if role else None, "group": {"name": team} if team else None})
    else:
        out["gen_ai"] = {"agent": {"id": principal, "name": principal}}
    return out


def _merge(base: dict, extra: dict) -> dict:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def ecs_decision(e: dict[str, Any]) -> dict[str, Any]:
    _, _, severity = _ACTION[e["action"]]
    types = {"block": ["denied"], "redact": ["allowed", "change"]}.get(e["action"], ["allowed"])
    out = _ecs_base(e, "event", ["api"], types, e["action"], "controllayer.audit")
    _merge(out, _ecs_person(e["principal"], e.get("owner"), e["team"], e["role"]))
    first = next((f for f in e["findings"] if f["action"] == e["action"]), None)
    _merge(
        out,
        {
            "event": _drop_none(
                {
                    "outcome": "success" if e["status_code"] < 400 else "failure",
                    "reason": e["reason"] or None,
                    "severity": severity,
                }
            ),
            "message": e["reason"] or e["action"],
            "source": _ip(e),
            "rule": {"name": f"{first['control']}/{first['category']}", "ruleset": PRODUCT["uid"]} if first else {},
            "gen_ai": _drop_none(
                {
                    "request": {"model": e["model"]} if e.get("model") else None,
                    "provider": {"name": e["provider"]} if e.get("provider") else None,
                    "tool": {"name": e["tool"]} if e.get("tool") else None,
                }
            ),
            "controllayer": _drop_none(
                {
                    "request_id": e["request_id"],
                    "principal": e["principal"],
                    "channel": e["channel"],
                    "direction": e["direction"],
                    "policy_version": e["policy_version"],
                    "text": e.get("text"),
                    "text_sha256": e.get("text_sha256"),
                    "raw_text": e.get("raw_text"),
                    "findings": e["findings"],
                }
            ),
        },
    )
    return _drop_none(out)


def ecs_alert(a: dict[str, Any]) -> dict[str, Any]:
    out = _ecs_base(a, "alert", ["intrusion_detection"], ["info"], "insider_risk_alert", "controllayer.alerts")
    _merge(out, _ecs_person(a["principal"], a.get("owner"), a.get("team")))
    _merge(
        out,
        {
            "event": {"risk_score": a["score"], "reason": a["reason"], "severity": _RISK_SEVERITY[a["level"]]},
            "message": a["reason"],
            "rule": {"name": "insider_risk", "ruleset": PRODUCT["uid"]},
            "controllayer": _drop_none(
                {
                    "request_id": a.get("request_id"),
                    "principal": a["principal"],
                    "level": a["level"],
                    "score": a["score"],
                    "channel": a.get("channel"),
                    "direction": a.get("direction"),
                    "tool": a.get("tool"),
                    "action": a.get("action"),
                    "findings": a.get("findings"),
                }
            ),
        },
    )
    return _drop_none(out)


def ecs_note(n: dict[str, Any]) -> dict[str, Any]:
    kind = n["kind"]
    category, types = (["configuration"], ["change"]) if kind == "suspend" else (["iam"], ["user", "change"])
    out = _ecs_base(n, "event", category, types, kind, "controllayer.audit")
    target = n.get("agent") or n.get("principal")
    _merge(
        out,
        {
            "event": {"outcome": "success"},
            "message": kind,
            "source": _ip(n),
            "user": _drop_none(
                {
                    "name": None if n.get("integration") else n["actor"],
                    "id": None if n.get("integration") else n["actor"],
                    "target": {"name": target} if target else None,
                }
            ),
            "controllayer": _drop_none({k: v for k, v in n.items() if k not in ("ts", "src_ip")}),
        },
    )
    return _drop_none(out)


_ECS = {"decision": ecs_decision, "alert": ecs_alert, "note": ecs_note}
