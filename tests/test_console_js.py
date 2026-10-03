"""The security console's scripts, executed in QuickJS against a seeded 2,000-person org (see jsconsole.py)."""

import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from controllayer import seed
from controllayer.config import parse_policy
from controllayer.gateway.app import create_app

from .conftest import ROOT
from .jsconsole import Console

MAX_HTML = 250_000  # a view never dumps the whole org into the DOM


@pytest.fixture(scope="module")
def big_org(tmp_path_factory):
    d = tmp_path_factory.mktemp("org2k")
    text = (ROOT / "policy.yaml").read_text()
    (d / "policy.yaml").write_text(text)
    (d / "feeds").mkdir()
    (d / "feeds" / "signatures.json").write_text((ROOT / "feeds" / "signatures.json").read_text())
    out = seed.build(parse_policy(text), 2000, 30, 1, time.time())
    (d / "data").mkdir()
    (d / "data" / "org.json").write_text(json.dumps(out["directory"]))
    (d / "data" / "history.json").write_text(json.dumps(out["history"]))
    return TestClient(create_app(d / "policy.yaml", watch=False))


def links(html: str) -> list[str]:
    return [h.replace("&amp;", "&") for h in re.findall(r'href="([^"]+)" data-nav', html)]


def test_overview_renders_and_every_link_lands_on_a_view(big_org):
    page = Console(big_org, "/security?ui=next")
    html = page.html
    assert not page.errors(), page.errors()
    for title in ("Spend over time", "Budget used", "Insider risk", "Cost by service", "Spend per person", "Teams"):
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
    page = Console(big_org, "/security?ui=next")
    week = next(h for h in links(page.html) if "period=7" in h)
    page.nav(week)
    assert "period=7" in page.search
    assert page.requests[-1].endswith("/admin/analytics/overview?period=7")
    assert "last 7 days" in page.html and not page.errors()


def test_token_is_sent_and_kept_only_when_given(big_org):
    page = Console(big_org, "/security?ui=next&token=t0k")
    assert "token=t0k" in "".join(links(page.html))
    plain = Console(big_org, "/security?ui=next")
    assert all("token" not in h for h in links(plain.html))


def test_classic_console_still_renders(big_org):
    page = Console(big_org, "/security")
    assert "Security Console" in page.html and not page.errors()
