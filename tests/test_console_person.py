"""The person drill-down (views/person.js) in QuickJS against a seeded 2,000-person org with grants,
overrides and signals (see jsconsole.py). Every action goes through the real click handler."""

import copy
import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from controllayer.config import parse_policy
from controllayer.gateway.app import create_app

from .conftest import org_copy
from .jsconsole import Console

MAX_HTML = 250_000


@pytest.fixture(scope="module")
def seeded(seeded_org):
    """The seeded org's state, where one heavy agent gets an expired grant to renew."""
    d, out = seeded_org(2000)
    state = copy.deepcopy(out["state"])
    resources = parse_policy((d / "policy.yaml").read_text()).resources

    def capped(rid: str) -> bool:  # renewing it sets an expiry to check
        return rid not in state["suspended"] and resources[rid].max_grant_hours is not None

    # The owner of the agent with most grants: several pills of every kind.
    agent = max(sorted(state["grants"]), key=lambda a: (any(map(capped, state["grants"][a])), len(state["grants"][a])))
    rid = next(r for r in sorted(state["grants"][agent]) if capped(r))
    state["grants"][agent][rid]["expires_at"] = time.time() - 3600
    return {"dir": d, "agent": agent, "expired": rid, "state": state}


@pytest.fixture
def org(seeded, tmp_path):
    """A fresh app per test: actions write state.json, so each test gets its own."""
    return TestClient(create_app(org_copy(seeded["dir"], tmp_path / "org", seeded["state"]), watch=False))


class Recording(Console):
    """Also records each request's JSON body, to check the payload an action sends."""

    def _fetch(self, method, path, body, headers):
        self.bodies = getattr(self, "bodies", [])
        self.bodies.append((method, path, json.loads(body) if body else None))
        return super()._fetch(method, path, body, headers)

    @property
    def sent(self) -> list[tuple]:
        """The writes so far: (method, path, body)."""
        return [b for b in self.bodies if b[0] != "GET"]


def buttons(html: str, act: str) -> list[dict[str, str]]:
    """The data-* attributes of every enabled control with data-act=act, as .act() takes them."""
    out = []
    for tag in re.findall(r"<button\b[^>]*>", html):
        attrs = dict(re.findall(r'data-([a-z-]+)="([^"]*)"', tag))
        if attrs.get("act") == act and " disabled" not in tag:
            out.append({k: v.replace("&amp;", "&").replace("&quot;", '"') for k, v in attrs.items()})
    return out


def owner_of(client: TestClient, agent: str) -> str:
    return client.get(f"/admin/analytics/person/{agent}").json()["person"]["owner"]


def grants(client: TestClient, agent: str) -> dict:
    return {g["resource"]: g for g in client.get("/admin/grants").json()["grants"] if g["agent"] == agent}


def page_of(client: TestClient, pid: str) -> Recording:
    page = Recording(client, f"/security?person={pid}")
    assert not page.errors(), page.errors()
    return page


def test_heavy_person_renders_every_section(org, seeded):
    pid = owner_of(org, seeded["agent"])
    page = page_of(org, pid)
    html = page.html
    for title in (
        "Spend over time",
        "Spend by service and model",
        "Agents and access",
        "Insider risk",
        "Recent decisions",
    ):
        assert title in html
    assert f"/admin/analytics/person/{pid}?period=30" in page.requests[-1]
    assert "AUTO" in html and "override" in html and len(html) < MAX_HTML
    assert "view=audit&amp;principal=" + pid in html  # the full audit log, filtered to them
    assert buttons(html, "revoke") and buttons(html, "grant") and buttons(html, "renew")
    assert html.count('data-act="grant"') <= 40  # entitled resources only, not the catalogue
    assert page.js.eval("document.title").startswith(org.get(f"/admin/analytics/person/{pid}").json()["person"]["name"])


def test_top_spender_and_agent_pages_render_and_links_land(org):
    top = org.get("/admin/analytics/people?per_page=1&sort=-usd").json()["rows"][0]["id"]
    agent = org.get("/admin/analytics/people?kind=agent&per_page=1&sort=-usd").json()["rows"][0]["id"]
    for pid in (top, agent):
        page = page_of(org, pid)
        assert "<svg" in page.html and len(page.html) < MAX_HTML
        for href in sorted({h.replace("&amp;", "&") for h in re.findall(r'href="([^"]+)" data-nav', page.html)}):
            page.nav(href)
            assert not page.errors(), (pid, href, page.errors())
    assert "agent of" in page_of(org, agent).html


def test_heavy_pages_fit_two_viewports(org, seeded):
    layout = pytest.importorskip("tests.layout")
    top = org.get("/admin/analytics/people?per_page=1&sort=-usd").json()["rows"][0]["id"]
    for pid in (top, owner_of(org, seeded["agent"])):
        page = page_of(org, pid)
        assert not page.inspection
        assert page.height <= layout.BUDGET, (pid, page.height)


def test_unknown_person_is_a_message_not_a_failure(org):
    page = page_of(org, "no.such.person")
    assert 'No person or agent "no.such.person"' in page.html
    assert "view=people&amp;q=no.such.person" in page.html
    assert page.js.eval("document.title").startswith("Unknown person")


def test_grant_pill_grants_the_first_scope_for_the_resource_maximum(org, seeded):
    agent = seeded["agent"]
    page = page_of(org, owner_of(org, agent))
    btn = next(b for b in buttons(page.html, "grant") if b["agent"] == agent)
    assert btn["rid"] not in grants(org, agent)
    res = next(r for r in org.get(f"/admin/analytics/person/{agent}").json()["resources"] if r["id"] == btn["rid"])
    hours = res["max_grant_hours"]
    assert btn["scopes"] == res["scopes"][0] and btn["hours"] == ("" if hours is None else f"{hours:g}")
    page.act(btn)
    assert not page.errors(), page.errors()
    assert page.sent == [
        ("POST", "/admin/grants", {"agent": agent, "resource": btn["rid"], "scopes": [btn["scopes"]], "hours": hours})
    ]
    g = grants(org, agent)[btn["rid"]]
    assert g["active"] and g["scopes"] == [btn["scopes"]]
    assert {"agent": agent, "rid": btn["rid"], "act": "revoke"}.items() <= next(
        b for b in buttons(page.html, "revoke") if b["rid"] == btn["rid"] and b["agent"] == agent
    ).items()


def test_active_pill_revokes_and_scope_pills_patch(org, seeded):
    agent = seeded["agent"]
    page = page_of(org, owner_of(org, agent))
    scope = next(b for b in buttons(page.html, "scopes") if b["agent"] == agent)
    page.act(scope)
    assert not page.errors(), page.errors()
    assert page.sent == [("PATCH", f"/admin/grants/{agent}/{scope['rid']}", {"scopes": scope["scopes"].split(",")})]
    assert grants(org, agent)[scope["rid"]]["scopes"] == sorted(scope["scopes"].split(","))
    revoke = next(b for b in buttons(page.html, "revoke") if b["agent"] == agent and b["rid"] == scope["rid"])
    page.act(revoke)
    assert not page.errors(), page.errors()
    assert page.sent[-1] == ("DELETE", f"/admin/grants/{agent}/{scope['rid']}", {})
    assert scope["rid"] not in grants(org, agent)


def test_expired_grant_renews_with_its_scopes(org, seeded):
    agent, rid = seeded["agent"], seeded["expired"]
    before = grants(org, agent)[rid]
    assert not before["active"]
    page = page_of(org, owner_of(org, agent))
    assert "expired" in page.html
    renew = [b for b in buttons(page.html, "renew") if b["agent"] == agent and b["rid"] == rid]
    assert len(renew) == 2  # the pill and the link
    page.act(renew[0])
    assert not page.errors(), page.errors()
    after = grants(org, agent)[rid]
    assert after["active"] and after["scopes"] == before["scopes"]
    assert after["expires_at"] > time.time()
    assert page.sent[0][2]["scopes"] == before["scopes"]


def test_expired_grant_revokes(org, seeded):
    agent, rid = seeded["agent"], seeded["expired"]
    page = page_of(org, owner_of(org, agent))
    revoke = next(b for b in buttons(page.html, "revoke") if b["agent"] == agent and b["rid"] == rid)
    page.act(revoke)
    assert not page.errors(), page.errors()
    assert page.sent == [("DELETE", f"/admin/grants/{agent}/{rid}", {})]
    assert rid not in grants(org, agent)
    assert not [b for b in buttons(page.html, "renew") if b["agent"] == agent and b["rid"] == rid]


def test_suspended_resource_is_greyed_and_inert(org):
    rows = org.get("/admin/analytics/people?team=engineering&per_page=50").json()["rows"]
    pid = next(r["id"] for r in rows if r["agents"])
    html = page_of(org, pid).html
    vercel = re.search(r'<button[^>]*data-rid="vercel"[^>]*>', html)
    assert vercel and " disabled" in vercel.group(0) and "acl-disabled" in vercel.group(0)
    assert "suspended by security" in vercel.group(0)


def test_risk_level_override_and_back_to_auto(org):
    pid = org.get("/admin/analytics/people?per_page=1&sort=-usd").json()["rows"][0]["id"]
    page = page_of(org, pid)
    page.act({"act": "level", "v": "restricted"})
    assert not page.errors(), page.errors()
    assert page.sent == [("POST", f"/admin/risk/{pid}", {"level": "restricted"})]
    r = org.get(f"/admin/analytics/person/{pid}").json()["risk"]
    assert r["manual"]["level"] == "restricted" and r["level"] == "restricted"
    assert 'aria-pressed="true" data-act="level" data-v="restricted"' in page.html
    page.act({"act": "level", "v": "auto"})
    assert not page.errors(), page.errors()
    assert page.sent[-1] == ("POST", f"/admin/risk/{pid}", {"level": "auto"})
    r = org.get(f"/admin/analytics/person/{pid}").json()["risk"]
    assert r["manual"] is None
    assert 'aria-pressed="true" data-act="level" data-v="auto"' in page.html


def test_reset_score_and_dismiss_signal(org, seeded):
    state = seeded["state"]
    pid = sorted(state["signals"])[0]
    risk = org.get(f"/admin/analytics/person/{pid}").json()["risk"]
    assert risk["score"] > 0 and risk["signals"]
    page = page_of(org, pid)
    assert buttons(page.html, "reset")
    page.act({"act": "reset"})
    assert not page.errors(), page.errors()
    assert page.sent == [("POST", f"/admin/risk/{pid}/reset", {})]
    assert org.get(f"/admin/analytics/person/{pid}").json()["risk"]["score"] == 0
    assert not buttons(page.html, "reset")  # disabled at zero

    dismiss = buttons(page.html, "dismiss")
    assert [b["src"] for b in dismiss] == [s["source"] for s in risk["signals"]]
    page.act(dismiss[0])
    assert not page.errors(), page.errors()
    assert page.sent[-1] == ("DELETE", f"/admin/risk/{pid}/signal/{dismiss[0]['src']}", {})
    assert not org.get(f"/admin/analytics/person/{pid}").json()["risk"]["signals"]
    assert "External signals" not in page.html


def test_failed_action_shows_the_reason(org, seeded):
    agent = seeded["agent"]
    page = page_of(org, owner_of(org, agent))
    page.act({"act": "grant", "agent": agent, "rid": "no-such-resource", "scopes": "read", "hours": ""})
    assert "not entitled" in " ".join(page.errors())
