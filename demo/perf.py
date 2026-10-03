"""Latency of every admin GET on a seeded org, with the response cache off (every call is cold).

    python -m seed --data-dir data/demo --people 5000
    python demo/perf.py --data-dir data/demo            # p50 / max per endpoint, slowest first
    python demo/perf.py --data-dir data/demo --budget 300   # exit 1 if any p50 is over budget

Writes nothing to the data directory except what the endpoints themselves log (viewing a person's events
records the admin's stated reason).
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def endpoints(c) -> list[str]:
    """Every admin GET with realistic parameters, ids taken from the data."""
    inc = c.get("/api/admin/incidents", params={"limit": 1}).json()["incidents"]
    people = c.get("/api/admin/people", params={"sort": "usd", "limit": 1}).json()["rows"]
    rich = people[0]["principal"] if people else "alice"
    paths = [
        "/api/admin/overview",
        "/api/admin/summary",
        "/api/admin/usage",
        "/api/admin/usage?by=department&days=30",
        "/api/admin/usage?by=team&days=30&department=Engineering",
        "/api/admin/usage?by=workflow,model&days=7",
        "/api/admin/menu",
        "/api/admin/principals",
        "/api/admin/principals/frank/events?reason=perf",
        "/api/admin/actions",
        "/api/admin/leases",
        "/api/admin/leases?department=Engineering&resource=vm",
        "/api/admin/incidents",
        "/api/admin/incidents?status=open&department=Engineering&limit=50",
        "/api/admin/incidents/summary",
        "/api/admin/detections",
        "/api/admin/requests",
        "/api/admin/requests?status=pending&department=Engineering",
        "/api/admin/timeseries",
        "/api/admin/timeseries?by=department",
        "/api/admin/timeseries?by=source&days=90",
        "/api/admin/timeseries?metric=tokens&by=team&department=Engineering",
        "/api/admin/timeseries?metric=events&by=kind&days=30",
        "/api/admin/adherence",
        "/api/admin/adherence?by=department",
        "/api/admin/adherence?by=workflow&team=payments",
        "/api/admin/adherence?by=day&department=Sales%20%26%20Support",
        "/api/admin/people/alice",
        "/api/admin/people/frank",
        f"/api/admin/people/{rich}",
        "/api/admin/value",
        "/api/admin/value?by=department",
        "/api/admin/value?by=team&department=Engineering",
        "/api/admin/activity",
        "/api/admin/activity?interesting=1&limit=100",
        "/api/admin/activity?department=Finance",
        "/api/admin/events",
        "/api/admin/policy",
        "/api/admin/catalog",
        "/api/admin/grants",
        "/api/admin/grants?envelope=1&live=1",
        "/api/admin/export/backstage",
        "/api/admin/export/focus?days=1",
        "/api/admin/org",
        "/api/admin/org?days=7",
        "/api/admin/org?days=90",
        "/api/admin/outliers?department=Engineering",
        "/api/admin/outliers?days=30&team=payments",
        "/api/admin/usage?by=workflow&days=30&compare=1",
        "/api/admin/incidents?status=attention&q=a",
        "/api/admin/requests?envelope=1&status=decided&order=oldest",
        "/api/admin/leases?zombies=1",
        "/api/admin/org/teams?q=sales&sort=adherence_prev",
        "/api/admin/org/teams",
        "/api/admin/org/teams?department=Engineering&sort=risk",
        "/api/admin/org/unit?kind=department&name=Engineering",
        "/api/admin/org/unit?kind=team&name=payments",
        "/api/admin/org/unit?kind=department&name=Sales%20%26%20Support&days=7",
        "/api/admin/people",
        "/api/admin/people?q=kowal",
        "/api/admin/people?department=Finance&sort=usd&status=active",
        "/api/admin/outliers",
        "/api/admin/outliers?days=30&department=Engineering",
    ]
    if inc:
        paths.append(f"/api/admin/incidents/{inc[0]['id']}")
    return paths


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data/demo")
    ap.add_argument("--policy", default=str(ROOT / "policy.yaml"))
    ap.add_argument("--n", type=int, default=5, help="calls per endpoint")
    ap.add_argument("--budget", type=float, default=300.0, help="ms; endpoints over it are marked")
    args = ap.parse_args()
    os.environ["ACL_ADMIN_CACHE_SECONDS"] = "0"  # cold: no response cache

    from fastapi.testclient import TestClient

    from controllayer.gateway.app import create_app

    t0 = time.perf_counter()
    app = create_app(args.policy, watch=False, data_dir=args.data_dir)
    start_ms = (time.perf_counter() - t0) * 1000
    c = TestClient(app, headers={"x-admin-token": app.state.store.policy.identity.admin_token or ""})
    rows = []
    for path in endpoints(c):
        lat = []
        for _ in range(args.n):
            t = time.perf_counter()
            r = c.get(path)
            lat.append((time.perf_counter() - t) * 1000)
            if r.status_code != 200:
                print(f"!! {path}: {r.status_code} {r.text[:200]}", file=sys.stderr)
                break
        rows.append((statistics.median(lat), max(lat), lat[0], path, len(r.content)))
    print(f"startup {start_ms:.0f} ms  ({len(app.state.layer.usage.people)} people)")
    print(f"{'p50':>8} {'max':>8} {'first':>8}  {'KB':>6}  endpoint")
    over = 0
    for p50, mx, first, path, size in sorted(rows, reverse=True):
        mark = " <-- over budget" if p50 > args.budget else ""
        over += bool(mark)
        print(f"{p50:8.1f} {mx:8.1f} {first:8.1f}  {size / 1024:6.1f}  {path}{mark}")
    print(f"{len(rows)} endpoints, {over} over {args.budget:.0f} ms")
    sys.exit(1 if over else 0)


if __name__ == "__main__":
    main()
