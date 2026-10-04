"""The Redteam sidecar (redteam/) against this gateway: its admin-API contract, and the console proxy."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from tests.conftest import ADMIN

REDTEAM = Path(__file__).resolve().parent.parent / "redteam"
sys.path.insert(0, str(REDTEAM))

from shield_redteam.config import Config  # noqa: E402
from shield_redteam.core.target import GatewayTarget  # noqa: E402
from shield_redteam.core.types import Case  # noqa: E402
from shield_redteam.poligon import corpus  # noqa: E402
from shield_redteam.poligon.runner import run  # noqa: E402
from shield_redteam.poligon.score import report  # noqa: E402
from shield_redteam.server.app import create_app as create_sidecar  # noqa: E402


def target(client: TestClient) -> GatewayTarget:
    return GatewayTarget("http://testserver", ADMIN, client=client)


def test_every_admin_call_the_sidecar_makes_matches_the_gateway(client):
    t = target(client)
    fp = dict(seg.split("=", 1) for seg in t.fingerprint().split("|"))
    assert set(fp) == {"policy", "feed", "signatures", "semantic", "controls", "risk_levels"}
    assert "secrets:1block0" in fp["controls"] and fp["risk_levels"] == "all normal"
    info = t.describe()
    assert info["policy"]["version"] and info["policy_doc"]["identity"]
    d = t.evaluate(Case("x", "attack", "secrets", "my key is AKIAIOSFODNN7EXAMPLE", principal="alice"))
    assert d.stopped and not d.error
    client.post("/v1/guard", json={"direction": "input", "text": "hello"}, headers={"x-api-key": "dev-alice-key"})
    assert isinstance(t.events(10), list)
    assert all(e["channel"] == "sdk" for e in t.events(10, channel="sdk"))
    assert isinstance(t.grants(), list)


def test_shipped_corpus_runs_clean_against_the_shipped_policy(client):
    cases = corpus.load_dir(REDTEAM / "corpus")
    rep = report(run(target(client), cases, workers=1))
    assert rep["errors"] == 0 and rep["posture"] is not None and rep["attacks"]["stopped"] > 0


def test_console_proxy_reaches_the_sidecar_behind_admin_auth(client, monkeypatch, tmp_path):
    monkeypatch.setenv("ACL_REDTEAM_URL", "http://127.0.0.1:9")  # nothing listens there
    down = client.get("/api/admin/redteam/state")
    assert down.status_code == 503 and down.json()["error"] == "redteam not running"
    assert client.get("/api/admin/redteam/state", headers={"x-admin-token": "wrong"}).status_code == 401

    (tmp_path / "a.yaml").write_text(
        "kind: attack\ncategory: secrets\ncases:\n  - {id: a1, text: AKIAIOSFODNN7EXAMPLE}\n"
    )
    cfg = Config(tmp_path / "redteam.yaml", {"poligon": {"corpus": str(tmp_path), "mutate": False}})
    sidecar = create_sidecar(cfg, target=target(client), background=False)
    sidecar.state.poligon.run_now()
    client.app.state.redteam_transport = httpx.ASGITransport(app=sidecar)
    state = client.get("/api/admin/redteam/state")
    assert state.status_code == 200 and state.json()["poligon"]["report"]["posture"] == 100.0
    assert client.get("/admin/redteam/stage").json()["outcomes"][0]["id"] == "a1"
    assert "Posture score" in client.get("/api/admin/redteam/report.md").text
