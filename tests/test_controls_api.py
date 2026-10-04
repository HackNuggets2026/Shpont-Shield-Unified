"""Live control edits through the admin overlay: PATCH /admin/controls/{name} and strictness profiles."""

import yaml


def controls(c):
    return {r["name"]: r for r in c.get("/admin/summary").json()["controls"]}


def test_edit_applies_and_shows_in_summary(client):
    before = controls(client)["prompt_injection"]
    assert before["threshold"] == {"action": "block", "value": 0.85}
    r = client.patch("/admin/controls/prompt_injection", json={"mode": "warn", "shadow": True, "threshold": 0.7})
    assert r.status_code == 200, r.text
    after = controls(client)["prompt_injection"]
    assert (after["mode"], after["shadow"]) == ("warn", True)
    assert after["threshold"] == {"action": "warn", "value": 0.6}  # mode warn: its own line is the main one
    assert controls(client)["pii"]["enabled"] is True
    assert client.patch("/admin/controls/pii", json={"enabled": False}).status_code == 200
    assert controls(client)["pii"]["enabled"] is False


def test_threshold_follows_main_line(client):
    assert client.patch("/admin/controls/data_exfiltration", json={"threshold": 0.95}).status_code == 200
    assert controls(client)["data_exfiltration"]["threshold"] == {"action": "block", "value": 0.95}
    assert client.patch("/admin/controls/pii_model", json={"threshold": 0.8}).status_code == 200
    assert controls(client)["pii_model"]["threshold"] == {"action": "min_score", "value": 0.8}


def test_invalid_edits_are_rejected(client):
    r = client.patch("/admin/controls/pii", json={"mode": "obliterate"})
    assert r.status_code == 400 and "mode must be one of" in r.json()["error"]
    r = client.patch("/admin/controls/prompt_injection", json={"threshold": 1.5})
    assert r.status_code == 400 and "rejected" in r.json()["error"]
    assert client.patch("/admin/controls/secrets", json={"threshold": 0.5}).status_code == 400
    assert client.patch("/admin/controls/pii", json={"colour": "red"}).status_code == 400
    assert client.patch("/admin/controls/nope", json={"enabled": False}).status_code == 404
    assert controls(client)["pii"]["mode"] == "redact"


def test_edit_is_persisted_in_the_overlay(client, policy_dir):
    assert client.patch("/admin/controls/secrets", json={"mode": "redact"}).status_code == 200
    overlay = yaml.safe_load((policy_dir / "data/admin-overlay.yaml").read_text())
    assert overlay["secrets"] == {"mode": "redact"}
    assert client.patch("/admin/controls/harmful_request", json={"shadow": True}).status_code == 200
    overlay = yaml.safe_load((policy_dir / "data/admin-overlay.yaml").read_text())
    assert overlay["semantic_controls"]["harmful_request"] == {"shadow": True}


def test_profiles(client):
    assert client.get("/admin/controls/profile").json()["active"] == "balanced"
    assert client.post("/admin/controls/profile", json={"profile": "strict"}).status_code == 200
    rows = controls(client)
    assert rows["pii"]["mode"] == "block" and rows["prompt_injection"]["threshold"]["value"] == 0.7
    assert client.get("/admin/controls/profile").json()["active"] == "strict"
    client.patch("/admin/controls/pii", json={"mode": "warn"})
    assert client.get("/admin/controls/profile").json()["active"] is None
    assert client.post("/admin/controls/profile", json={"profile": "balanced"}).status_code == 200
    assert client.get("/admin/controls/profile").json()["active"] == "balanced"
    assert client.post("/admin/controls/profile", json={"profile": "lax"}).status_code == 400


def test_edit_is_enforced_at_once(client):
    key = "AKIAIOSFODNN7EXAMPLE"
    try_it = lambda: client.post("/admin/try", json={"principal": "alice", "text": f"my key {key}"})  # noqa: E731
    assert try_it().json()["action"] == "block"
    assert client.patch("/admin/controls/secrets", json={"shadow": True}).status_code == 200
    assert try_it().json()["action"] != "block"
