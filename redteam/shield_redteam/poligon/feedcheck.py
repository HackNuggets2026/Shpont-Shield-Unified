"""Self-test of a signature feed: every signature proves itself before it is trusted.

A feed entry may carry its own vectors: `tests: {match: [...], clean: [...]}`. A signature that
does not compile, misses one of its `match` strings or fires on a `clean` one is reported broken.
"""

from __future__ import annotations

import re
from typing import Any


def check_feed(feed: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for entry in feed.get("signatures", []):
        problems = []
        try:
            pattern = re.compile(entry["pattern"], re.IGNORECASE if "i" in entry.get("flags", "") else 0)
        except (KeyError, re.error) as e:
            rows.append({"id": entry.get("id", "?"), "ok": False, "vectors": 0, "problems": [f"does not compile: {e}"]})
            continue
        tests = entry.get("tests") or {}
        for s in tests.get("match", []):
            if not pattern.search(s):
                problems.append(f"misses its own example: {s[:60]!r}")
        for s in tests.get("clean", []):
            if pattern.search(s):
                problems.append(f"fires on clean text: {s[:60]!r}")
        vectors = len(tests.get("match", [])) + len(tests.get("clean", []))
        rows.append({"id": entry.get("id", "?"), "ok": not problems, "vectors": vectors, "problems": problems})
    return {
        "feed_version": str(feed.get("feed_version", "unknown")),
        "signatures": len(rows),
        "broken": sum(not r["ok"] for r in rows),
        "self_tested": sum(r["vectors"] > 0 for r in rows),
        "rows": rows,
    }
