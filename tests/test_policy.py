"""Centralised policy: hot reload, validation, strictness levels, team overrides."""

import pytest

from controllayer.config import parse_policy

from .conftest import KEYS, ROOT, chat, edit_policy, guard

CARD = "card 4111 1111 1111 1111"


def reload(client):
    r = client.post("/admin/policy/reload")
    return r


def test_shipped_policy_is_valid():
    p = parse_policy((ROOT / "policy.yaml").read_text())
    assert p.semantic_controls and p.pii.entities and p.version


def test_disabling_a_control_takes_effect_live(client, policy_dir):
    assert guard(client, CARD).json()["action"] == "redact"
    old = client.get("/admin/policy").json()["version"]
    edit_policy(policy_dir, lambda p: p["pii"].update(enabled=False))
    assert reload(client).json()["ok"]
    assert client.get("/admin/policy").json()["version"] != old
    assert guard(client, CARD + " again").json()["action"] == "allow"


def test_poll_picks_up_file_edit_without_admin_call(client, policy_dir):
    def mutate(p):
        p["pii"]["mode"] = "block"
        p["pii"]["entities"]["credit_card"] = "block"

    edit_policy(policy_dir, mutate)
    store = client.app.state.store
    store._mtime = 0  # force the watcher to see a change regardless of filesystem mtime granularity
    store.poll()
    assert guard(client, CARD).json()["action"] == "block"


@pytest.mark.parametrize("mode,expected", [("block", "redact"), ("redact", "redact"), ("warn", "warn"), ("log", "log")])
def test_mode_caps_the_strongest_action(client, policy_dir, mode, expected):
    edit_policy(policy_dir, lambda p: p["pii"].update(mode=mode))
    reload(client)
    assert guard(client, f"{CARD} ({mode})").json()["action"] == expected


def test_threshold_change_changes_semantic_verdict(make_client, policy_dir):
    from controllayer.decision import ScriptedBackend

    sb = ScriptedBackend({"prompt_injection": [("borderline", 0.9)]})
    c = make_client(backend=sb, mutate=lambda p: p["semantic"].update(deep_model=None))
    assert guard(c, "borderline one").json()["action"] == "block"
    edit_policy(policy_dir, lambda p: p["semantic_controls"]["prompt_injection"]["thresholds"].update(block=0.95))
    reload(c)
    assert guard(c, "borderline two").json()["action"] == "warn"


def test_new_semantic_control_added_purely_in_config(make_client, policy_dir):
    from controllayer.decision import ScriptedBackend

    sb = ScriptedBackend({"competitor_talk": [("Globex", 0.95)]})
    c = make_client(backend=sb)
    assert guard(c, "compare us with Globex").json()["action"] == "allow"
    edit_policy(policy_dir, lambda p: p["semantic_controls"].update(competitor_talk={
        "mode": "warn", "directions": ["input"], "instructions": "Does it discuss competitors?",
        "thresholds": {"warn": 0.8}}))
    reload(c)
    assert guard(c, "compare us with Globex now").json()["action"] == "warn"


@pytest.mark.parametrize("bad", [
    lambda p: p["pii"].update(mood="block"),  # misspelled key
    lambda p: p["pii"].update(mode="obliterate"),  # unknown action
    lambda p: p["teams"].update(x={"controls": {"nonexistent": {"mode": "log"}}}),
    lambda p: p["semantic_controls"]["harmful_request"]["actions"].update(unknown_cat="block"),
])
def test_invalid_edit_is_rejected_and_old_policy_stays_live(client, policy_dir, bad):
    before = client.get("/admin/policy").json()["version"]
    edit_policy(policy_dir, bad)
    r = reload(client)
    assert r.status_code == 422
    info = client.get("/admin/policy").json()
    assert info["version"] == before and info["last_error"]
    assert guard(client, CARD).json()["action"] == "redact"


def test_team_override_finance_blocks_cards(client):
    assert chat(client, CARD, who="alice").status_code == 200
    r = chat(client, CARD, who="bob")
    assert r.status_code == 403
    assert "pii/credit_card" in r.json()["error"]["message"]


def test_team_override_merges_instead_of_replacing(client):
    # finance overrides credit_card/iban only; email stays at the base "log"
    body = client.post("/v1/guard", json={"text": "mail jan@example.com"}, headers=KEYS["bob"]).json()
    assert body["action"] == "log"


def test_team_override_platform_only_logs_pii(client):
    body = client.post("/v1/guard", json={"text": CARD}, headers=KEYS["ops"]).json()
    assert body["action"] == "log"


def test_shadow_mode_on_deterministic_control(client, policy_dir):
    edit_policy(policy_dir, lambda p: p["secrets"].update(shadow=True))
    reload(client)
    body = guard(client, "AKIAIOSFODNN7EXAMPLE").json()
    assert body["action"] == "allow"
    assert body["findings"][0]["proposed"] == "block" and body["findings"][0]["shadow"]
