"""The security console's scripts, executed in QuickJS against a seeded 2,000-person org (see jsconsole.py)."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from controllayer.gateway.app import create_app

from .conftest import org_copy
from .jsconsole import Console
from .layout import BUDGET

MAX_HTML = 250_000  # a view never dumps the whole org into the DOM


@pytest.fixture(scope="module")
def big_org(seeded_org, tmp_path_factory):
    d, _ = seeded_org(2000)
    return TestClient(create_app(org_copy(d, tmp_path_factory.mktemp("org2k")), watch=False))


def links(html: str) -> list[str]:
    return [h.replace("&amp;", "&") for h in re.findall(r'href="([^"]+)" data-nav', html)]


def test_overview_renders_and_every_link_lands_on_a_view(big_org):
    page = Console(big_org, "/security")
    html = page.html
    assert not page.errors(), page.errors()
    for title in ("Spend over time", "Budget used", "Insider risk", "Cost by service", "Spend per person", "Top teams"):
        assert title in html
    assert html.count("<svg") >= 2 and len(html) < MAX_HTML
    assert "Demo mode - no authentication" in html  # the shipped policy runs in demo mode
    assert "token=" not in html  # opened without a token, no link carries one
    targets = sorted(set(links(html)))
    assert any("view=people" in t and "budget=over" in t for t in targets)
    assert any("item=service%3A" in t for t in targets) and any("person=" in t for t in targets)
    for href in targets:
        page.nav(href)
        assert not page.errors(), (href, page.errors())
        assert len(page.html) < MAX_HTML


def test_period_switch_reloads_with_that_period(big_org):
    page = Console(big_org, "/security")
    week = next(h for h in links(page.html) if "period=7" in h)
    page.nav(week)
    assert "period=7" in page.search
    assert page.requests[-1].endswith("/admin/analytics/overview?period=7")
    assert "last 7 days" in page.html and not page.errors()


def test_token_is_sent_and_kept_only_when_given(big_org):
    page = Console(big_org, "/security?token=t0k")
    assert "token=t0k" in "".join(links(page.html))
    plain = Console(big_org, "/security")
    assert all("token" not in h for h in links(plain.html))


def test_root_is_the_console_and_me_is_the_employee_panel(big_org):
    console = Console(big_org, "/")
    assert "Spend over time" in console.html and not console.errors()
    panel = Console(big_org, "/me")
    assert "My AI Workspace" in panel.html and "Spend over time" not in panel.html and not panel.errors()


@pytest.fixture(scope="module")
def org500(seeded_org, tmp_path_factory):
    """500 people with grants, overrides and signals: the size the height budget is set for."""
    d, out = seeded_org(500, 3)
    return TestClient(create_app(org_copy(d, tmp_path_factory.mktemp("org500"), out["state"]), watch=False))


def test_every_page_fits_two_screens_unless_it_is_an_inspection_page(org500):
    """Estimated with tests/layout.py: no browser exists here, so this is the markup's arithmetic, not
    a measured layout."""
    top = org500.get("/admin/analytics/overview").json()["spend"]["top"][0]["id"]
    tabs = [v for v in ("overview", "people", "risk", "resources", "controls", "audit", "playground")]
    pages = [f"/security?view={v}" for v in tabs] + [f"/security?person={top}"]
    page = Console(org500, pages[0])
    seen = {}
    for url in pages:
        page.nav(url)
        assert not page.errors(), (url, page.errors())
        seen[page.view] = (page.height, page.inspection)
        if not page.inspection:
            assert page.height <= BUDGET, (url, page.height)
    assert set(seen) == {*tabs, "person"}


def test_overview_risk_is_a_top_five_with_profiles_and_next_steps(org500):
    page = Console(org500, "/security")
    html = page.html
    top = org500.get("/admin/analytics/overview").json()["risk"]
    assert top["attention"] > 0 and f"{top['attention']}</span> people need attention" in html
    assert html.count(">View profile</a>") == len(top["top"]) == 5
    for p in top["top"]:
        assert f"person={p['id']}" in html and p["reason"].split(" (")[0].replace(">", "&gt;") in html
    assert "Review watch list" in html and "view=risk" in html
    assert "Recent alerts" not in html  # no full lists on the overview


def test_logos_cover_every_service_and_model_family(big_org):
    page = Console(big_org, "/security")

    def logo(key):
        return page.js.eval(f"ACL.logo({json.dumps(key)})")

    from controllayer.services import SERVICES

    for key in SERVICES:
        html = logo("service:" + key)
        assert "acl-logo-text" not in html and "cdn.jsdelivr.net/npm/" in html, key  # a real logo, no letter
    assert "simple-icons@16.33.0/icons/stripe.svg" in logo("stripe")
    assert "postgresql.svg" in logo("postgres-prod") and "snowflake.svg" in logo("snowflake_query")
    assert "devicon@2.17.0/icons/salesforce/" in logo("salesforce-crm") and 'alt="Salesforce"' in logo("salesforce-crm")
    assert "devicon@2.17.0/icons/heroku/" in logo("heroku_get_logs")
    lobe = "icons-static-svg@1.95.1/icons/"
    assert lobe + "claude-color.svg" in logo("model:claude-sonnet-5") and lobe + "openai.svg" in logo("gpt-4o-mini")
    assert lobe + "meta-color.svg" in logo("llama3.2:3b") and lobe + "qwen-color.svg" in logo("qwen3:8b")
    assert lobe + "mistral-color.svg" in logo("mistral-large")
    assert 'aria-label="mock-model"' in logo("model:mock-model")  # unknown: a lettered badge with its own name
    assert "&lt;x&gt;" in logo("<x>")
