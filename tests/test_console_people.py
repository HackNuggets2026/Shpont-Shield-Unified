"""The People view (views/people.js) in QuickJS against the seeded 2,000-person org (see jsconsole.py)."""

import json
import re
import time
from urllib.parse import parse_qsl, urlsplit

from . import test_console_js
from .jsconsole import Console
from .layout import BUDGET
from .test_console_js import MAX_HTML, links

big_org = test_console_js.big_org  # the seeded 2,000-person org fixture

URL = "/security?ui=next&view=people"


def last_people_query(page: Console) -> dict[str, str]:
    path = next(r for r in reversed(page.requests) if r.startswith("GET /admin/analytics/people?")).split(" ", 1)[1]
    return dict(parse_qsl(urlsplit(path).query))


def row_ids(page: Console) -> list[str]:
    return re.findall(r'<tr data-href="/security\?[^"]*\bperson=([^"&]+)"', page.html)


def url_params(page: Console) -> dict[str, str]:
    return dict(parse_qsl(page.search.lstrip("?")))


def day(s: str) -> str:
    return time.strftime("%b ", time.strptime(s, "%Y-%m-%d")) + str(int(s[8:]))


def api(client, **params) -> dict:
    return client.get("/admin/analytics/people", params=params).json()


def test_lists_one_page_of_the_org_by_spend(big_org):
    page = Console(big_org, URL)
    assert not page.errors(), page.errors()
    html = page.html
    assert len(html) < MAX_HTML
    assert 'href="/security?ui=next&amp;view=people" data-nav aria-current="page">People</a>' in html  # a visible tab
    assert last_people_query(page) == {"period": "30"}
    expected = api(big_org, period=30)
    assert row_ids(page) == [r["id"] for r in expected["rows"]] and len(expected["rows"]) == 50
    total = f"{expected['total']:,}".replace(",", ",?")  # QuickJS has no Intl: no thousands separator
    assert re.search(total + " people<", html) and re.search("1-50 of " + total + "<", html)
    assert f"spend {day(expected['first'])} - {day(expected['last'])} (UTC), agents included" in html
    for col in ("Person", "Team", "Spend", "Budget used", "Top service or model", "Risk", "Blocked", "Last active"):
        assert f">{col}" in html
    assert "Spend ↓" in html  # the default sort is marked
    assert "acl-meter acl-m-over" in html  # the top spenders include someone over budget, drawn red
    assert not any("/admin/analytics/overview" in r for r in page.requests)
    s = expected["summary"]
    assert f'>Over budget</div>\n      <div class="acl-tile-value acl-t-bad">{s["bands"]["over"]}<' in html


def test_chip_counts_match_what_picking_them_gives(big_org):
    page = Console(big_org, f"{URL}&team=engineering")
    html = page.html
    for key, value in (("budget", "over"), ("budget", "near"), ("risk", "watch"), ("risk", "elevated")):
        chip = rf'href="([^"]*{key}={value})" data-nav[^>]*>.*?<span class="color-fg-muted">(\d+)</span></a>'
        m = re.search(chip, html)
        assert m, (key, value)
        page.nav(m.group(1).replace("&amp;", "&"))
        q = last_people_query(page)
        assert q["team"] == "engineering" and q[key] == value
        assert api(big_org, **q)["total"] == int(m.group(2)), (key, value)
        page.nav(f"{URL}&team=engineering")
    # an option nobody matches is greyed, not a link
    page.nav(f"{URL}&q=adam.varga")
    greyed = r'<span class="btn btn-sm BtnGroup-item" aria-disabled="true"[^>]*><i [^>]*></i>Restricted'
    assert re.search(greyed, page.html)
    assert not any("risk=restricted" in h for h in links(page.html))


def test_filter_chips_and_selects_send_their_params(big_org):
    page = Console(big_org, URL)
    restricted = next(h for h in links(page.html) if "risk=restricted" in h)
    page.nav(restricted)
    assert last_people_query(page)["risk"] == "restricted" and not page.errors()
    assert row_ids(page) == [r["id"] for r in api(big_org, risk="restricted")["rows"]]
    assert 'aria-current="true" href="/security?ui=next&amp;view=people&amp;risk=restricted"' in page.html

    over = next(h for h in links(page.html) if "budget=over" in h)
    page.nav(over)
    q = last_people_query(page)
    assert (q["risk"], q["budget"]) == ("restricted", "over")  # filters combine

    page.nav(f"{URL}&page=3")
    watch = next(h for h in links(page.html) if "risk=watch" in h)
    assert watch == f"{URL}&risk=watch"  # picking a filter starts again at page 1
    page.change({"param": "team"}, "sales")
    assert url_params(page)["team"] == "sales" and "page" not in url_params(page)
    q = last_people_query(page)
    assert q["team"] == "sales" and "page" not in q
    assert set(re.findall(r"team=([a-z-]+)\" data-nav>", page.html)) == {"sales"}

    page.change({"act": "item"}, "service:salesforce")
    assert url_params(page)["item"] == "service:salesforce" and url_params(page)["sort"] == "-item"
    q = last_people_query(page)
    assert (q["item"], q["sort"], q["team"]) == ("service:salesforce", "-item", "sales")
    assert ">Salesforce ↓<" in page.html  # the item's spend column, sorted
    page.change({"act": "item"}, "")
    assert "item" not in url_params(page) and "sort" not in url_params(page)
    assert not page.errors(), page.errors()


def test_search_replaces_q_and_resets_the_page(big_org):
    page = Console(big_org, f"{URL}&page=2")
    page.type("q", "  ada ")
    assert url_params(page)["q"] == "ada" and "page" not in url_params(page)
    assert last_people_query(page)["q"] == "ada"
    assert row_ids(page) == [r["id"] for r in api(big_org, q="ada")["rows"]]
    assert 'value="ada"' in page.html
    page.type("q", "")
    assert "q" not in url_params(page) and "q" not in last_people_query(page)


def test_sort_headers_toggle_direction(big_org):
    page = Console(big_org, URL)
    assert f"{URL}&sort=-used" in links(page.html)  # the header, unsorted, sorts descending first
    page.nav(f"{URL}&sort=-used")
    assert last_people_query(page) == {"sort": "-used", "period": "30"} and "Budget used ↓" in page.html
    assert f"{URL}&sort=used" in links(page.html)
    page.nav(f"{URL}&sort=used")
    assert last_people_query(page) == {"sort": "used", "period": "30"} and "Budget used ↑" in page.html
    assert row_ids(page) == [r["id"] for r in api(big_org, sort="used")["rows"]]
    idle = [r["id"] for r in api(big_org, sort="used")["rows"] if not r["usd"]]
    assert idle and page.html.count('class="acl-greyed" title="No spend in this period"') == len(idle)


def test_pager_and_rows_per_page(big_org):
    page = Console(big_org, URL)
    first = row_ids(page)
    page.nav(next(h for h in links(page.html) if h.endswith("page=2")))
    assert last_people_query(page)["page"] == "2"
    second = row_ids(page)
    assert len(second) == 50 and not set(first) & set(second)
    assert "51-100 of" in page.html
    page.change({"param": "per_page"}, "25")
    assert last_people_query(page) == {"per_page": "25", "period": "30"}
    assert row_ids(page) == first[:25]
    assert '<option value="25" selected>' in page.html


def test_overview_links_land_filtered(big_org):
    page = Console(big_org, "/security?ui=next")
    targets = sorted({h for h in links(page.html) if "view=people" in h})
    assert len(targets) >= 10
    seen = set()
    for href in targets:
        page.nav(href)
        assert not page.errors(), (href, page.errors())
        want = {"period": "30", **{k: v for k, v in parse_qsl(urlsplit(href).query) if k not in ("ui", "view")}}
        q = last_people_query(page)
        assert q == want, href
        seen |= set(want)
        expected = api(big_org, **q)
        assert row_ids(page) == [r["id"] for r in expected["rows"]], href
        if "min_usd" in want:
            assert "spend $" in page.html and "Remove this filter" in page.html
    assert {"team", "budget", "risk", "item", "min_usd", "max_usd", "end"} <= seen


def test_row_click_opens_the_person(big_org):
    page = Console(big_org, f"{URL}&team=sales&page=2")
    pid = row_ids(page)[3]
    href = f"/security?ui=next&person={pid}"
    assert f'<tr data-href="{href.replace("&", "&amp;")}"' in page.html
    page.js.eval(
        "__fire('click', { button: 0, preventDefault: function () {}, target: __target(function (sel) {"
        f" return sel === 'tr[data-href]' ? __el('TR', {{ 'data-href': {json.dumps(href)} }}) : null; }}) }})"
    )
    page.settle()
    assert url_params(page) == {"ui": "next", "person": pid}
    assert page.requests[-1].startswith(f"GET /admin/analytics/person/{pid}?")
    assert not page.errors(), page.errors()


def test_agents_and_bad_filters(big_org):
    page = Console(big_org, URL)
    page.nav(next(h for h in links(page.html) if "kind=agent" in h))
    assert last_people_query(page)["kind"] == "agent"
    rows = api(big_org, kind="agent")["rows"]
    assert row_ids(page) == [r["id"] for r in rows]
    assert f'of <a class="Link--primary text-bold" href="/security?ui=next&amp;person={rows[0]["owner"]}"' in page.html
    assert ">Agent</a></th>" in page.html and "agents included" not in page.html

    bad = Console(big_org, f"{URL}&risk=bogus")
    assert "Cannot list people: risk must be one of" in bad.html and "Clear all filters" in bad.html
    assert not json.loads(bad.js.eval("JSON.stringify(__errors)"))


def test_is_an_inspection_page_with_one_line_rows(big_org):
    page = Console(big_org, f"{URL}&per_page=25")
    assert page.inspection and "Database inspection" in page.html
    short = page.height
    assert short <= BUDGET  # 25 rows, the filters and the summary fit two screens
    page.nav(f"{URL}&per_page=100")
    assert len(row_ids(page)) == 100 and len(page.html) < MAX_HTML
    assert (page.height - short) / 75 <= 36  # every row stays one line
    assert '<span class="acl-logo' in page.html  # top service or model carries its logo
