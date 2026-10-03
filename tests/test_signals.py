"""External risk signals: raise-only, capped, expiring, scoped to their integration; security's override wins."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from controllayer.gateway.app import create_app

from .conftest import ADMIN, edit_policy, guard
from .test_export import SCHEMA, conforms

WAZUH = {"Authorization": "Bearer wz-secret"}  # max_level watch (policy.yaml)
EDR = {"Authorization": "Bearer edr-secret"}  # max_level restricted (added below)
CARD = "card 4111 1111 1111 1111"


@pytest.fixture
def c(make_client, monkeypatch):
    monkeypatch.setenv("ACL_WAZUH_TOKEN", "wz-secret")
    monkeypatch.setenv("ACL_EDR_TOKEN", "edr-secret")

    def add_edr(p):
        p["identity"]["integrations"]["edr"] = {"token_env": "ACL_EDR_TOKEN", "max_level": "restricted"}

    return make_client(mutate=add_edr)


def signal(c, pid="alice", who=WAZUH, **body):
    body.setdefault("ttl_seconds", 600)
    # Integrations send only their own token; the client's default admin header must not help.
    return c.post(f"/admin/risk/{pid}/signal", json=body, headers={**who, "x-admin-token": ""})


def row(c, pid):
    return {r["principal"]: r for r in c.get("/admin/risk").json()["principals"]}.get(pid)


def level(c, pid):
    r = row(c, pid)
    return r["level"] if r else "normal"


def raise_score_to_watch(c, pid="alice"):
    for i in range(4):  # 4 blocks: 40 points
        guard(c, f"key AKIAIOSFODNN7EXAMP{i}A", who=pid)
    assert row(c, pid)["computed"] == "watch"


def test_signal_raises_the_auto_level_and_tightens_enforcement(c):
    assert guard(c, CARD).json()["action"] == "redact"
    r = signal(c, level="watch", reason="usb mass storage", source="usb")
    assert r.status_code == 200, r.text
    assert r.json() | {"expires_at": None} == {
        "principal": "alice",
        "source": "wazuh/usb",
        "requested": "watch",
        "level": "watch",
        "expires_at": None,
        "withdrawn": False,
        "evicted": None,
    }
    alice = row(c, "alice")
    assert (alice["computed"], alice["auto"], alice["level"]) == ("normal", "watch", "watch")
    assert [(s["source"], s["level"], s["reason"]) for s in alice["signals"]] == [
        ("wazuh/usb", "watch", "usb mass storage")
    ]
    assert guard(c, CARD + " again").json()["action"] == "block"  # watch_controls apply
    agent = c.post("/v1/guard", json={"text": CARD}, headers={"Authorization": "Bearer alice-agent-key"})
    assert agent.json()["action"] == "block"  # agents follow their owner


def test_signal_expires(c):
    signal(c, level="watch", ttl_seconds=600)
    sig = c.app.state.layer.state.signals["alice"]["wazuh"]
    sig["expires_at"] = time.time() - 1
    assert level(c, "alice") == "normal"
    assert row(c, "alice") is None or row(c, "alice")["signals"] == []


def test_signal_never_lowers_the_score_based_level(c):
    raise_score_to_watch(c)
    assert signal(c, level="normal").status_code == 200
    assert level(c, "alice") == "watch"
    assert signal(c, score=0).json()["level"] == "normal"
    assert level(c, "alice") == "watch"


def test_manual_override_wins_over_signals_in_both_directions(c):
    c.post("/admin/risk/alice", json={"level": "normal", "reason": "cleared after review"})
    assert signal(c, who=EDR, level="restricted").status_code == 200
    assert level(c, "alice") == "normal"  # security's decision stands
    assert row(c, "alice")["auto"] == "restricted"  # what AUTO would be, shown to security
    assert c.app.state.layer.state.watch["alice"]["level"] == "normal"  # a signal never clears it
    c.post("/admin/risk/alice", json={"level": "auto"})
    assert level(c, "alice") == "restricted"  # back on auto, the signal is a floor
    c.post("/admin/risk/bob", json={"level": "restricted"})
    signal(c, "bob", level="watch")
    assert level(c, "bob") == "restricted"


def test_max_level_caps_the_signal(c):
    r = signal(c, level="restricted")
    assert (r.json()["requested"], r.json()["level"]) == ("restricted", "watch")
    assert level(c, "alice") == "watch"
    lv = c.app.state.store.policy.insider_risk.levels
    r = signal(c, who=EDR, score=lv.restricted)
    assert (r.json()["requested"], r.json()["level"]) == ("restricted", "restricted")
    assert signal(c, who=EDR, score=lv.watch - 0.01).json()["level"] == "normal"


def test_each_source_replaces_only_its_own_signal(c):
    signal(c, who=EDR, level="restricted")
    signal(c, level="watch")
    signal(c, level="watch", source="usb")
    # The wazuh token cannot name edr's signal: its sources are always prefixed with its own name.
    r = signal(c, level="normal", source="edr")
    assert r.json()["source"] == "wazuh/edr" and r.json()["withdrawn"] is False
    assert level(c, "alice") == "restricted"
    assert signal(c, level="normal").json()["withdrawn"] is True
    assert [s["source"] for s in row(c, "alice")["signals"]] == ["edr", "wazuh/usb"]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer nope"},
        {"Authorization": f"Bearer {ADMIN}"},
        {"x-admin-token": ADMIN},
    ],
)
def test_signal_needs_an_integration_token(c, headers):
    r = c.post(
        "/admin/risk/alice/signal", json={"level": "watch", "ttl_seconds": 60}, headers={"x-admin-token": "", **headers}
    )
    assert r.status_code == 401
    assert level(c, "alice") == "normal"


def test_integration_token_is_not_an_admin_token(c):
    assert c.get("/admin/risk", headers={**WAZUH, "x-admin-token": ""}).status_code == 401
    assert c.delete("/admin/risk/alice/signal/wazuh", headers={**WAZUH, "x-admin-token": ""}).status_code == 401


def test_unset_or_admin_equal_token_disables_the_integration(c, monkeypatch):
    monkeypatch.delenv("ACL_WAZUH_TOKEN")
    assert signal(c, level="watch").status_code == 401
    monkeypatch.setenv("ACL_WAZUH_TOKEN", ADMIN)
    assert signal(c, who={"Authorization": f"Bearer {ADMIN}"}, level="watch").status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"level": "watch", "score": 50},
        {"ttl_seconds": 60},
        {"level": "high"},
        {"score": -1},
        {"score": True},
        {"level": "watch", "ttl_seconds": None},
        {"level": "watch", "ttl_seconds": 0},
        {"level": "watch", "ttl_seconds": 24 * 3600 + 1},  # wazuh: max_ttl_hours 24
        {"level": "watch", "ttl_seconds": "60"},
        {"level": "watch", "source": "a/b"},
    ],
)
def test_signal_validation(c, body):
    r = c.post("/admin/risk/alice/signal", json=body, headers={**WAZUH, "x-admin-token": ""})
    assert r.status_code == 400, r.text
    assert level(c, "alice") == "normal"


def test_non_finite_ttl_is_rejected(c):
    r = c.post(
        "/admin/risk/alice/signal",
        content=b'{"level": "watch", "ttl_seconds": NaN}',
        headers={**WAZUH, "x-admin-token": "", "content-type": "application/json"},
    )
    assert r.status_code == 400


def test_unknown_principal(c):
    assert signal(c, "mallory", level="watch").status_code == 404


def test_signals_are_audited_and_raise_a_silent_alert(c, policy_dir):
    signal(c, level="watch", reason="impossible travel")
    signal(c, level="normal")
    notes = [n for n in c.app.state.layer.audit.notes if n["kind"] == "risk_signal"]
    assert [(n["actor"], n["principal"], n["source"], n["level"]) for n in notes] == [
        ("wazuh", "alice", "wazuh", "watch"),
        ("wazuh", "alice", "wazuh", "normal"),
    ]
    assert notes[0]["reason"] == "impossible travel" and notes[0]["ttl_seconds"] == 600
    alerts = c.get("/admin/alerts").json()["alerts"]
    assert [a["reason"] for a in alerts] == ["level normal -> watch: signal from wazuh"]
    assert alerts[0]["channel"] == "signal"
    assert "signal from wazuh" in (policy_dir / "data/security-alerts.jsonl").read_text()
    ocsf = [json.loads(line) for line in c.get("/admin/audit/export?format=ocsf").text.splitlines()]
    sig = next(d for d in ocsf if d["class_uid"] == 3004)
    assert sig["actor"] == {"application": {"name": "wazuh"}} and sig["entity"]["uid"] == "alice"
    assert sig["risk_level"] == "Medium"
    assert all(conforms(d, SCHEMA["classes"][str(d["class_uid"])]) == [] for d in ocsf)


def test_security_dismisses_a_signal(c):
    signal(c, level="watch", source="usb")
    assert c.delete("/admin/risk/alice/signal/wazuh/usb", headers={"x-admin-token": ""}).status_code == 401
    assert c.delete("/admin/risk/alice/signal/wazuh/usb").json()["dismissed"] is True
    assert level(c, "alice") == "normal"
    assert c.delete("/admin/risk/alice/signal/wazuh/usb").status_code == 404
    assert c.app.state.layer.audit.notes[-1]["kind"] == "risk_signal_dismissed"


def test_signals_survive_a_restart(c, policy_dir):
    signal(c, level="watch")
    again = TestClient(create_app(policy_dir / "policy.yaml", watch=False), headers={"x-admin-token": ADMIN})
    assert level(again, "alice") == "watch"


def reload_with(c, policy_dir, mutate):
    edit_policy(policy_dir, mutate)
    assert c.post("/admin/policy/reload").json()["ok"]


def test_removing_an_integration_drops_its_stored_signals_at_once(c, policy_dir):
    signal(c, who=EDR, level="restricted")
    signal(c, level="watch", source="usb")
    assert level(c, "alice") == "restricted"
    reload_with(c, policy_dir, lambda p: p["identity"]["integrations"].pop("edr"))  # e.g. its token leaked
    assert level(c, "alice") == "watch"
    assert [s["source"] for s in row(c, "alice")["signals"]] == ["wazuh/usb"]
    assert guard(c, CARD).json()["action"] == "block"  # watch_controls, not restricted
    reload_with(c, policy_dir, lambda p: p["identity"].update(integrations={}))
    assert level(c, "alice") == "normal"
    assert guard(c, "hello").json()["action"] == "allow"


def test_lowering_max_level_or_ttl_caps_stored_signals(c, policy_dir):
    signal(c, who=EDR, level="restricted", ttl_seconds=48 * 3600)
    assert level(c, "alice") == "restricted"
    reload_with(c, policy_dir, lambda p: p["identity"]["integrations"]["edr"].update(max_level="watch"))
    assert level(c, "alice") == "watch"
    assert row(c, "alice")["signals"][0]["level"] == "watch"
    stored = c.app.state.layer.state.signals["alice"]["edr"]
    assert stored["level"] == "restricted"  # the record is untouched; only its effect is capped
    reload_with(c, policy_dir, lambda p: p["identity"]["integrations"]["edr"].update(max_ttl_hours=1))
    assert row(c, "alice")["signals"][0]["expires_at"] == pytest.approx(stored["at"] + 3600)
    stored["at"] -= 3601  # sent more than the new max_ttl_hours ago
    assert level(c, "alice") == "normal"


def test_sources_per_integration_and_principal_are_capped(c, policy_dir):
    reload_with(c, policy_dir, lambda p: p["identity"]["integrations"]["edr"].update(max_sources=3))
    edr = lambda pid="alice", **kw: signal(c, pid, who=EDR, **kw)  # noqa: E731
    assert edr(level="watch", source="a", ttl_seconds=900).json()["evicted"] is None
    assert edr(level="restricted", source="b").status_code == 200
    assert edr(level="watch", source="c", ttl_seconds=600).status_code == 200
    assert edr(level="watch", source="a", reason="renewed", ttl_seconds=900).json()["evicted"] is None  # replacing
    # Full: a new source displaces the weakest, soonest-expiring one, never a stronger one.
    r = edr(level="watch", source="d")
    assert (r.status_code, r.json()["evicted"]) == (200, "edr/c")
    assert sorted(c.app.state.layer.state.signals["alice"]) == ["edr/a", "edr/b", "edr/d"]
    assert c.app.state.layer.audit.notes[-1]["evicted"] == "edr/c"
    assert edr(level="restricted", source="e").json()["evicted"] == "edr/d"
    assert edr(level="restricted", source="f").json()["evicted"] == "edr/a"
    r = edr(level="watch", source="g")  # every live source is stronger
    assert r.status_code == 429 and "max_sources" in r.text
    assert sorted(c.app.state.layer.state.signals["alice"]) == ["edr/b", "edr/e", "edr/f"]
    assert level(c, "alice") == "restricted"
    # Per integration and per principal; expired ones do not count.
    assert signal(c, level="watch", source="x").json()["evicted"] is None
    assert edr("bob", level="watch", source="g").json()["evicted"] is None
    c.app.state.layer.state.signals["alice"]["edr/b"]["expires_at"] = time.time() - 1
    assert edr(level="watch", source="g").json()["evicted"] is None


@pytest.mark.parametrize("value, status", [(1, 200), (64, 200), (0, 422), (65, 422), ("many", 422)])
def test_max_sources_is_validated(c, policy_dir, value, status):
    edit_policy(policy_dir, lambda p: p["identity"]["integrations"]["wazuh"].update(max_sources=value))
    assert c.post("/admin/policy/reload").status_code == status
