"""The stage view's data: the control layer as rings of defence, and where each payload ended.

Rings are ordered outermost first, in the order the control layer evaluates them. A segment is one
control; its state comes from the live policy, so a control switched off in the config goes dark.
"""

from __future__ import annotations

from typing import Any

from ..core.types import STOPPING, Outcome

RINGS = [
    ("access", "Access", ["auth", "models", "tool_access"]),
    ("budget", "Budgets", ["budget"]),
    ("deterministic", "Deterministic", ["secrets", "pii", "signatures"]),
    ("context", "Contextual PII", ["pii_model"]),
    ("semantic", "Semantic", []),  # filled from the policy: one segment per semantic control
]
LABELS = {
    "auth": "Identity",
    "models": "Model allowlist",
    "tool_access": "Tool access",
    "budget": "Rate, tokens, cost",
    "secrets": "Secrets",
    "pii": "PII patterns",
    "signatures": "Attack signatures",
    "pii_model": "Privacy model",
}
# Control named in a finding -> segment that draws it.
ALIAS = {"model": "models", "resource": "tool_access", "insider_risk": "auth", "pii_override": "pii_model"}
# Where an attack of a class is expected to be caught; used to aim a payload that nothing stopped.
INTENDED = {
    "prompt_injection": "prompt_injection",
    "indirect_injection": "prompt_injection",
    "exfiltration": "data_exfiltration",
    "harmful_request": "harmful_request",
    "secrets": "secrets",
    "pii": "pii",
    "historical_exploits": "signatures",
}


def _state(enabled: bool, mode: str, shadow: bool) -> str:
    if not enabled or mode == "allow":
        return "off"
    if shadow:
        return "shadow"
    return "active" if mode in STOPPING else "monitor"


def rings(info: dict[str, Any]) -> list[dict[str, Any]]:
    controls = {c["name"]: c for c in info.get("controls", [])}
    doc = info.get("policy_doc") or {}
    semantic_on = (info.get("semantic") or {}).get("backend", "heuristic") != "off"

    def seg(sid: str) -> dict[str, Any]:
        c = controls.get(sid)
        if c:
            on = c["enabled"] and (c["kind"] != "semantic" or semantic_on)
            return {
                "id": sid,
                "label": LABELS.get(sid, sid.replace("_", " ")),
                "state": _state(on, c["mode"], c["shadow"]),
                "mode": c["mode"],
            }
        state, mode = "active", "block"
        if sid == "auth":
            state = "active" if (doc.get("identity") or {}).get("require_auth", True) else "off"
        elif sid == "models":
            state = "active" if (doc.get("models") or {}).get("allowed") else "off"
        elif sid == "budget":
            b = doc.get("budgets") or {}
            state = _state(b.get("enabled", True), "block", b.get("shadow", False))
        elif sid == "pii_model":
            m = doc.get("pii_model") or {}
            mode = m.get("mode", "redact")
            state = _state(m.get("backend", "stub") != "off" and m.get("enabled", True), mode, m.get("shadow", False))
        return {"id": sid, "label": LABELS.get(sid, sid), "state": state, "mode": mode}

    out = []
    for rid, label, segs in RINGS:
        ids = segs or [n for n, c in controls.items() if c["kind"] == "semantic"]
        out.append({"id": rid, "label": label, "segments": [seg(s) for s in ids]})
    return out


def locate(control: str, ring_list: list[dict[str, Any]]) -> tuple[int, str]:
    """Ring index and segment id for a control name; unknown names belong to the semantic ring."""
    sid = ALIAS.get(control, control)
    for i, ring in enumerate(ring_list):
        if any(s["id"] == sid for s in ring["segments"]):
            return i, sid
    last = len(ring_list) - 1
    segs = ring_list[last]["segments"]
    return last, segs[0]["id"] if segs else sid


def place(findings: list[dict[str, Any]], category: str, ring_list: list[dict[str, Any]]) -> tuple[int, str]:
    """Where a payload stopped: the outermost control that redacted or blocked it. A payload that
    nothing stopped is aimed at the control expected to catch its class."""
    hits = [locate(f.get("control", ""), ring_list) for f in findings if f.get("action") in STOPPING]
    if hits:
        return min(hits)
    return locate(INTENDED.get(category, ""), ring_list)


def outcome_rows(outcomes: list[Outcome], ring_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for o in outcomes:
        ring, seg = place(o.decision.findings, o.case.category, ring_list)
        rows.append(
            {
                "id": o.case.id,
                "attack": o.case.must_stop,
                "stopped": o.decision.stopped,
                "category": o.case.category,
                "direction": o.case.direction,
                "ring": ring,
                "seg": seg,
                "rewrite": bool(o.case.parent),
                "action": o.decision.action,
                "text": o.case.text if len(o.case.text) <= 150 else o.case.text[:147] + "...",
            }
        )
    return rows
