"""Optional console theme and packaging of the console's static files."""

import fnmatch
import re
import tomllib

from .conftest import ROOT
from .jsconsole import Console

DASHBOARD = ROOT / "controllayer" / "dashboard"


def test_every_console_file_is_packaged():
    # The Docker image installs the package; a file outside package-data is missing in production.
    globs = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]["controllayer"]
    files = [p.relative_to(ROOT / "controllayer").as_posix() for p in DASHBOARD.rglob("*") if p.is_file()]
    missing = [f for f in files if not any(fnmatch.fnmatch(f, g) and f.count("/") == g.count("/") for g in globs)]
    assert files and missing == []


def test_flat_theme_is_opt_in_and_allowlisted(client):
    page = client.get("/security").text
    assert "/ui/themes/flat.css" in page and 'get("theme") === "flat"' in page
    assert client.get("/ui/themes/flat.css").status_code == 200


def test_theme_survives_navigation(client):
    page = Console(client, "/security?theme=flat")
    assert page.errors() == []
    assert all("theme=flat" in h for h in re.findall(r'href="([^"]+)" data-nav', page.html) if h.startswith("/"))
