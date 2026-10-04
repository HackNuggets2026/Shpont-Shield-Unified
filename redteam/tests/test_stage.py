"""The stage view: rings follow the live policy, and every payload lands on the control that stopped it."""

from __future__ import annotations

from shield_redteam.core.types import Case, Decision, Outcome
from shield_redteam.server.stage import locate, outcome_rows, place, rings


def _control(name, kind="deterministic", enabled=True, mode="block", shadow=False):
    return {"name": name, "kind": kind, "enabled": enabled, "mode": mode, "shadow": shadow, "hits": 0}


INFO = {
    "controls": [
        _control("secrets"),
        _control("pii", mode="redact"),
        _control("signatures"),
        _control("tool_access"),
        _control("prompt_injection", "semantic"),
        _control("off_policy_use", "semantic", mode="log", shadow=True),
    ],
    "semantic": {"backend": "heuristic"},
    "policy_doc": {"identity": {"require_auth": True}, "models": {"allowed": ["m"]}, "budgets": {"enabled": True}},
}


def _states(info):
    return {s["id"]: s["state"] for r in rings(info) for s in r["segments"]}


def test_rings_are_in_evaluation_order_with_semantic_controls_from_the_policy():
    rs = rings(INFO)
    assert [r["id"] for r in rs] == ["access", "budget", "deterministic", "context", "semantic"]
    assert [s["id"] for s in rs[4]["segments"]] == ["prompt_injection", "off_policy_use"]


def test_segment_state_follows_the_policy():
    st = _states(INFO)
    assert st["secrets"] == st["pii"] == st["prompt_injection"] == "active"
    assert st["off_policy_use"] == "shadow"
    weak = {**INFO, "controls": [_control("secrets", enabled=False), _control("pii", mode="log")]}
    assert _states(weak)["secrets"] == "off" and _states(weak)["pii"] == "monitor"


def test_switching_the_semantic_backend_off_darkens_the_whole_ring():
    off = {**INFO, "semantic": {"backend": "off"}}
    st = _states(off)
    assert st["prompt_injection"] == "off" and st["secrets"] == "active"


def test_budget_and_auth_states_come_from_the_policy_document():
    doc = {"identity": {"require_auth": False}, "models": {"allowed": []}, "budgets": {"enabled": False}}
    st = _states({**INFO, "policy_doc": doc})
    assert st["auth"] == st["models"] == st["budget"] == "off"


def test_payload_lands_on_the_outermost_control_that_stopped_it():
    rs = rings(INFO)
    findings = [
        {"control": "prompt_injection", "action": "block"},
        {"control": "secrets", "action": "redact"},
        {"control": "pii", "action": "log"},
    ]
    assert place(findings, "secrets", rs) == (2, "secrets")
    assert place([{"control": "budget", "action": "block"}], "", rs) == (1, "budget")
    assert place([{"control": "model", "action": "block"}], "", rs) == (0, "models")


def test_unstopped_attack_is_aimed_at_the_control_meant_to_catch_it():
    rs = rings(INFO)
    assert place([], "indirect_injection", rs) == (4, "prompt_injection")
    assert place([{"control": "pii", "action": "log"}], "historical_exploits", rs) == (2, "signatures")
    assert locate("some_new_semantic_control", rs)[0] == 4


def test_outcome_rows():
    rs = rings(INFO)
    o = Outcome(
        Case("a1~leet", "attack", "secrets", "x", parent="a1"),
        Decision("block", [{"control": "secrets", "action": "block"}]),
    )
    row = outcome_rows([o], rs)[0]
    assert (row["ring"], row["seg"], row["stopped"], row["rewrite"]) == (2, "secrets", True, True)
