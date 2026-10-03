"""The demo seeder: deterministic, separate from the server, and a dataset the gateway runs on."""

import re
import time

from fastapi.testclient import TestClient

from controllayer.gateway.app import create_app
from seed.world import seed

from .conftest import ADMIN, KEYS, ROOT, chat

NOW = 1790000000.0  # a Wednesday, 13:33 UTC


def test_same_seed_same_history(tmp_path):
    a = seed(ROOT / "policy.yaml", tmp_path / "a", days=10, rng_seed=7, now=NOW)
    b = seed(ROOT / "policy.yaml", tmp_path / "b", days=10, rng_seed=7, now=NOW)
    c = seed(ROOT / "policy.yaml", tmp_path / "c", days=10, rng_seed=8, now=NOW)
    assert a == b and a["usd"] > 0 and a["events"] > 1000
    assert c != a


def test_the_server_never_imports_the_seeder():
    for f in (ROOT / "controllayer").rglob("*.py"):
        assert not re.search(r"^\s*(from|import)\s+seed\b", f.read_text(), re.M), f


def test_gateway_runs_on_a_seeded_month(policy_dir, tmp_path):
    data = tmp_path / "demo"
    seed(policy_dir / "policy.yaml", data, days=30, rng_seed=42, now=time.time() - 60)
    c = TestClient(create_app(policy_dir / "policy.yaml", watch=False, data_dir=data), headers={"x-admin-token": ADMIN})
    people = {r["principal"]: r for r in c.get("/api/admin/principals").json()}
    assert len(people) >= 12
    assert people["frank"]["status"] == "quarantined" and people["frank"]["level"] == "quarantine"
    open_rules = {i["rule"] for i in c.get("/api/admin/incidents", params={"status": "open"}).json()["incidents"]}
    assert {"exfiltration", "probing"} <= open_rules
    inc = next(i for i in c.get("/api/admin/incidents").json()["incidents"] if i["rule"] == "exfiltration")
    d = c.get(f"/api/admin/incidents/{inc['id']}").json()
    assert sum(e["evidence"] for e in d["timeline"]) >= 2
    spend = c.get("/api/admin/timeseries", params={"by": "source", "days": 30}).json()
    assert {"gateway", "claude_code", "mcp", "billing_export", "report"} <= set(spend["series"])
    assert all(v > 0 for v in spend["totals"][-5:])
    adherence = c.get("/api/admin/adherence").json()["overall"]["adherence"]
    assert 0.9 < adherence < 1
    assert c.get("/api/admin/requests", params={"status": "pending"}).json()
    grants = c.get("/api/admin/grants", params={"live": True}).json()
    assert any(g["principal"] == "heidi" and g["resource"] == "prod_deploy" for g in grants)
    # The people used live in the demo still have budget today.
    for who in ("alice", "bob", "carol", "ops"):
        assert chat(c, "hello", who=who).status_code == 200, who
    me = c.get("/api/me/summary", headers={"x-admin-token": "", **KEYS["alice"]}).json()
    assert me["leases"] and me["by_workflow"]
