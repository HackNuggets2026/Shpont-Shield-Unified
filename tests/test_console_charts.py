"""Console charts: time series and distributions render as smooth, non-overshooting curves with a
hover crosshair; each point still drills down."""

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


@pytest.fixture(scope="module")
def org(tmp_path_factory):
    d = tmp_path_factory.mktemp("org200")
    text = (ROOT / "policy.yaml").read_text()
    (d / "policy.yaml").write_text(text)
    (d / "feeds").mkdir()
    (d / "feeds" / "signatures.json").write_text((ROOT / "feeds" / "signatures.json").read_text())
    out = seed.build(parse_policy(text), 200, 30, 1, time.time())
    (d / "data").mkdir()
    (d / "data" / "org.json").write_text(json.dumps(out["directory"]))
    (d / "data" / "history.json").write_text(json.dumps(out["history"]))
    return TestClient(create_app(d / "policy.yaml", watch=False))


def svg(html: str, label: str) -> str:
    m = re.search(r'<svg class="acl-chart"[^>]*aria-label="' + re.escape(label) + r'">(.*?)</svg>', html, re.S)
    assert m, f"no chart labelled {label!r}"
    return m.group(1)


def segments(d: str) -> list[tuple[float, list[tuple[float, float]]]]:
    """(start y, [control1, control2, end]) for each cubic segment of a path's first subpath."""
    first = re.match(r"M[^MZ]*", d).group(0)
    start = tuple(map(float, re.match(r"M([-\d.]+),([-\d.]+)", first).groups()))
    out, y0 = [], start[1]
    for c in re.findall(r"C([-\d.,\s]+?)(?=C|L|Z|$)", first):
        pts = [tuple(map(float, p.split(","))) for p in c.split()]
        out.append((y0, pts))
        y0 = pts[-1][1]
    return out


def curves(chart: str) -> list[str]:
    return re.findall(r'<path d="([^"]+)" fill="none"', chart)


def test_spend_over_time_is_a_smooth_area_with_a_crosshair_per_day(org):
    page = Console(org, "/security?period=30")
    assert page.errors() == []
    chart = svg(page.html, "Spend over time")
    assert "fill-opacity" in chart and curves(chart), "stacked areas with a stroked top line"
    assert all("C" in d for d in curves(chart)), "curves, not polylines"
    hovers = re.findall(
        r'<a href="([^"]+)" data-nav data-tip="[^"]+"><rect class="acl-hit"[^>]*/><line class="acl-xhair"', chart
    )
    assert len(hovers) == 30 and all("view=people" in h and "end=" in h for h in hovers)


def test_curves_never_overshoot_between_points(org):
    chart = svg(Console(org, "/security?period=30").html, "Spend over time")
    assert curves(chart)
    for d in curves(chart):
        for y0, (c1, c2, end) in segments(d):
            lo, hi = min(y0, end[1]), max(y0, end[1])
            assert lo - 0.11 <= c1[1] <= hi + 0.11 and lo - 0.11 <= c2[1] <= hi + 0.11


def test_spend_per_person_is_a_density_curve_that_stays_above_the_baseline(org):
    page = Console(org, "/security?period=30")
    chart = svg(page.html, "Spend per person")
    base = float(re.search(r'class="acl-base" x1="[^"]+" x2="[^"]+" y1="([-\d.]+)"', chart).group(1))
    (d,) = curves(chart)
    ys = [float(y) for y in re.findall(r"[ ,C]-?[\d.]+,([-\d.]+)", d)]
    assert ys and max(ys) <= base + 0.11, "an SVG y below the baseline would draw a negative count"
    assert 'class="acl-marker"' in chart and "p50" in chart and "p90" in chart
    bands = re.findall(r'<a href="([^"]+)" data-nav data-tip="[^"]+"><rect class="acl-hit"', chart)
    assert bands and all("min_usd=" in b and "max_usd=" in b for b in bands)


def test_person_spend_over_time_is_an_area(org):
    top = org.get("/admin/analytics/overview?period=30").json()["spend"]["top"][0]["id"]
    page = Console(org, f"/security?person={top}&period=30")
    assert page.errors() == []
    chart = svg(page.html, "Spend over time")
    assert curves(chart) and "<rect x=" not in chart.replace('class="acl-hit"', "")
