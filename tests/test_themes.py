"""Optional panel skins: served as static CSS, loaded by panel.html only for allowlisted ?theme= names."""

import json
import re

import pytest

FONTS = "https://fonts.googleapis.com/"
FAKE_DOM = """
function URLSearchParams(s) {
  var m = {};
  s.replace(/^\\?/, "").split("&").filter(Boolean).forEach(function (kv) {
    var i = kv.indexOf("=");
    m[decodeURIComponent(i < 0 ? kv : kv.slice(0, i))] = i < 0 ? "" : decodeURIComponent(kv.slice(i + 1));
  });
  this.get = function (k) { return Object.prototype.hasOwnProperty.call(m, k) ? m[k] : null; };
}
var attrs = {}, links = [];
var document = {
  documentElement: { setAttribute: function (k, v) { attrs[k] = v; } },
  createElement: function () { return {}; },
  head: { appendChild: function (l) { links.push(l.rel + " " + l.href); } },
};
"""


def test_mil_theme_served(client):
    r = client.get("/ui/themes/mil.css")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/css")
    assert ':root[data-acl-theme="mil"]' in r.text


def run_loader(html: str, search: str) -> dict:
    """Execute panel.html's inline loader script against a fake DOM; return what it changed."""
    quickjs = pytest.importorskip("quickjs")
    script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    ctx = quickjs.Context()
    ctx.eval(f"var location = {{ search: {json.dumps(search)} }};" + FAKE_DOM)
    ctx.eval(script)
    return json.loads(ctx.eval("JSON.stringify({ attrs: attrs, links: links })"))


@pytest.mark.parametrize("path", ["/security", "/me"])
def test_panel_loads_mil_theme_after_primer(client, path):
    html = client.get(path).text
    assert html.index("primer.css") < html.index("/ui/core.css") < html.index("<script>")
    out = run_loader(html, "?token=x&theme=mil")
    assert out["attrs"] == {"data-color-mode": "dark", "data-dark-theme": "dark", "data-acl-theme": "mil"}
    assert [link.split(" ", 1)[0] for link in out["links"]] == ["stylesheet", "stylesheet"]
    assert out["links"][0].split(" ", 1)[1].startswith(FONTS)
    assert out["links"][1] == "stylesheet /ui/themes/mil.css"


UNLISTED = [
    "",
    "?theme=",
    "?theme=MIL",
    "?theme=primer",
    "?theme=__proto__",
    "?theme=constructor",
    "?theme=toString",
    "?theme=../core",
]


@pytest.mark.parametrize("search", UNLISTED)
def test_panel_ignores_unlisted_theme(client, search):
    assert run_loader(client.get("/security").text, search) == {"attrs": {}, "links": []}
