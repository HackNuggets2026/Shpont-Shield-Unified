# Enterprise scale: contract between backend and frontend

Target: one install per company with 1,000 to 50,000 employees. The demo org is **ACME Bank**, with about
5,000 people in 6 departments and about 40 teams, seeded over 30 days. The console works with **organization
units** (departments, then teams), workflows and resources. Individual people appear only as top-N outliers,
in search, or after drilling into a team. The incident page keeps naming the person, because security needs a
name. The employee portal does not change.

All admin endpoints are under `/api/admin/*` and need the `x-admin-token` header. Money is in USD (floats),
time in unix seconds, and every window is in UTC days. Existing endpoints keep their shapes; new fields are
only ever added. The contract is frozen once the backend's milestone 1 has landed.

## Organization model

- **Directory.** A `people` table in the usage DB has these columns: `principal` (PK), `name`, `email`,
  `team`, `department`, `role`, `title`, `location`, `cost_center`. The seeder fills it. People listed under
  `identity.api_keys` in `policy.yaml` (the live-demo keys) are merged in. Their department comes from the
  new optional `department` field on the key, or from the `org.teams` mapping.
- **Org settings.** `policy.yaml` gets a new `org:` section: `{name: "ACME Bank", departments: {<dept>: {teams:
  [..], owner: ".."}}}`. A team listed nowhere belongs to department `"Unassigned"`.
- **Department everywhere.** `usage` and `events` gain a `department` column. It is filled at write time from
  the directory (Ingestor, ledger, leases, detections). `department` is a valid value for every
  `by=`/`group by` parameter: usage breakdown, timeseries, adherence and value.
- **Rollup rows.** `events` gains an `n` column (NULL means 1). A row with `n > 1` stands for `n` checks with
  the same day, person, kind, workflow and decision. The seeder writes the bulk of its history this way.
  Every count uses `SUM(COALESCE(n,1))`. The activity feed never returns rows with `n > 1`. The `usage` table
  already aggregates through its `requests` and token columns.
- **Performance budget.** On the 5k seed, every `/api/admin/*` GET answers in under 300 ms cold. Seeding
  takes under 60 s. Aggregate GETs sit behind a TTL cache (15 s) that any admin POST clears.

## New endpoints

### `GET /api/admin/org?days=30`
```json
{
  "name": "ACME Bank",
  "headcount": 5012, "active": 3870, "teams": 41, "departments_count": 6,
  "window": {"days": 30, "since": 1788000000},
  "totals": {"usd": 412345.6, "usd_prev": 380120.2, "tokens": 9.1e9, "checks": 1830000,
             "interventions": 23800, "adherence": 0.987, "incidents_open": 14, "people_at_risk": 9,
             "claude_code_users": 1410, "claude_code_usd": 160200.0, "usd_per_active": 106.5},
  "departments": [
    {"name": "Engineering", "owner": "cto-office", "headcount": 1820, "active": 1700, "teams": 14,
     "usd": 260000.0, "usd_prev": 241000.0, "usd_per_active": 152.9, "tokens": 5.2e9,
     "checks": 900000, "interventions": 9000, "adherence": 0.99, "incidents_open": 6, "people_at_risk": 4,
     "claude_code_users": 1200, "claude_code_usd": 150000.0, "top_workflow": "bugfix"}
  ]
}
```
`usd_prev` is the previous window of the same length, used for trend arrows. `active` counts people with
any metered usage in the window.

### `GET /api/admin/org/teams?department=&days=30&sort=usd|usd_per_active|adherence|risk|headcount&order=desc&limit=100&offset=0`
`{"total": 41, "rows": [{...the department fields above, plus "name" (the team), "department"}]}`

### `GET /api/admin/org/unit?kind=department|team&name=Engineering&days=30`
```json
{
  "kind": "department", "name": "Engineering", "metrics": {"...the same fields as a department row": 0},
  "spend": {"days": [], "series": {"bugfix": []}, "totals": []},
  "adherence_trend": {"days": [], "values": []},
  "by_workflow": [{"workflow": "bugfix", "usd": 0, "runs": 0, "p50": 0, "p90": 0}],
  "by_resource": [{"resource": "claude_code", "usd": 0, "tokens": 0, "minutes": 0}],
  "teams": [{"...team rows, departments only": 0}],
  "outliers": {"cost": [], "risk": []}
}
```

### `GET /api/admin/people?q=&department=&team=&status=&sort=risk|usd|tokens|name&order=desc&limit=50&offset=0&days=30`
`{"total": 5012, "rows": [principal_row + {"department", "name", "email", "usd", "tokens"}]}`. `q` matches id,
name or email as a prefix or substring. `status` is one of active, quarantined, revoked or limited
(budget_scale < 1). This replaces any need for a full list. `/api/admin/principals` stays for compatibility
but returns at most 200 rows: everyone restricted or at risk first, then the highest spend.

### `GET /api/admin/outliers?days=7&limit=10&department=&team=`
```json
{
  "cost": [{"principal": "", "name": "", "team": "", "department": "", "usd": 0, "team_median": 0, "ratio": 0}],
  "risk": [{"principal": "", "name": "", "team": "", "department": "", "risk": 0, "level": "", "status": "", "open_incidents": 0}],
  "growth": [{"principal": "", "team": "", "department": "", "usd": 0, "usd_prev": 0, "growth": 0}]
}
```
`cost` lists people whose spend in the window is at least 3 times their team's median, sorted by ratio.
`growth` compares the window with the previous window.

### `GET /api/admin/incidents/summary?days=30`
`{"by_rule": [{"rule", "open", "acknowledged", "resolved", "dismissed", "total"}], "by_department":
[{"department", "open", "total", "people_at_risk"}], "trend": {"days": [], "opened": [], "closed": []},
"auto_actions_24h": 0}`

### Changes to existing endpoints (additive)
- `GET /api/admin/incidents`: adds `limit`, `offset`, `department`, `rule`, `status`, `principal`, and
  returns `total` next to `incidents`. Each incident gains `department`, `team` and `name`.
- `GET /api/admin/leases`: adds `{summary: {by_resource: [{resource, open, zombies, running_usd}],
  by_department: [{department, open, zombies, running_usd}]}}`. `open` takes `limit` (default 100) and
  `department`/`resource` filters, plus `open_total`.
- `GET /api/admin/grants`: adds `limit`, `offset`, `department`, `resource`, a `total`, and
  `summary: {live, expiring_1h, by_resource: [{resource, live}]}`. To keep the array shape, these come only
  with `?envelope=1`; without it the response is the array as before.
- `GET /api/admin/requests`: adds `department`, `kind`, `limit` (default 200) and `offset`. Each row gains
  `department`, `team` and `name`.
- `GET /api/admin/activity`: adds `interesting=1`. It hides `check.*` rows that were allowed and rows with
  severity `info`, so the feed shows blocks, redactions, incidents, grants, zombie flags and Claude Code
  rejections. Rows gain `department`.
- `GET /api/admin/overview`: adds `headcount`, `active`, `org_name`.
- `by=department` and `principal` drill-down filters `department=` and `team=` work on `timeseries`,
  `adherence`, `usage` and `value`.

## Seeded org (`python -m seed --data-dir data/demo --days 30 --seed 42 --people 5000`)

| Department | ~People | Teams (examples) |
|---|---|---|
| Engineering | 1,800 | engineering (alice, dan, erin, frank, judy), payments, mobile, web, data-platform, sre, ... (14) |
| Platform & Security | 450 | platform (heidi, ops-agent), security, it-ops, ... (5) |
| Data & Analytics | 550 | analytics, ml, bi, ... (5) |
| Finance | 600 | finance (bob, grace, mallory), treasury, risk, ... (5) |
| Operations | 900 | customer-ops, compliance, procurement, ... (6) |
| Sales & Support | 700 | sales-emea, sales-amer, support, interns (carol, ivan), ... (6) |

- **Named demo people.** They keep their keys, their detailed per-event history and the frank story. Everyone
  else gets daily rollups. Workflow mix and Claude Code adoption depend on the department (engineering is
  heavy, sales is light chat), with a long tail of heavy users.
- **Risk.** About 150 historical incidents spread across the org and 10 to 15 open ones, plus frank's.
- **Resources.** About 40 open leases, a few of them zombies; about 60 grants, 12 of them live.
- **Requests.** About 25 pending and about 300 decided.
- **Budgets.** They scale with the org. The global and per-team limits in `policy.yaml` are raised to match,
  and the four live-demo teams keep headroom for today.

## Deviations (backend)

Small choices the contract left open, or where it could not be followed exactly.

- **Windows.** `days=N` means N whole UTC days, today included (`window.since` is midnight UTC N-1 days ago);
  `usd_prev` is the N days before that. The seeder writes the window before the seeded one as well (usage only,
  one row per person and working day, ~8% lower spend), so `usd_prev` and `growth` have something to compare.
- **`headcount`** counts people in the directory; **`active`** counts anyone with metered usage in the window
  (a principal outside the directory, e.g. `unattributed`, can be active without adding to headcount).
- **`people_at_risk`** is people with an open or acknowledged incident, or a risk score at or above `detections.response.alert`.
  The score alone halves every 2 h, so it would empty out during a demo.
- **`interventions`** are checks that ended in warn, redact or block; adherence is `1 - interventions/checks`.
- **Team rows** carry `owner` (the team's manager in the directory, else the department owner) and `teams: 1`.
- **`/admin/people`**: `status=limited` means status `active` with `budget_scale < 1`; `status=active` includes
  limited people. Rows also carry `title`. `q` is a case-insensitive substring of id, name or email.
- **Outliers.** `team_median` is the median spend of the team's members with any spend in the window (dormant
  people would otherwise pull it to zero); teams with fewer than 3 spenders have no median. `growth` ignores a
  previous window below $5 per 30 days.
- **Seeded `interns` team** holds only carol and ivan: its tiny budget is part of the live demo.
- **Cache.** The TTL is `ACL_ADMIN_CACHE_SECONDS` (default 15; 0 disables it, which the tests do).
- **Grants envelope.** `GET /admin/grants?envelope=1` returns `{total, grants, summary}`; `live=1` still
  filters. Grant rows (both shapes) gain `department`, `team` and `name`.
- **Leases.** `summary` covers every open lease, whatever the filters; `open` is newest first. Open and recent
  rows gain `department`, `team` and `name`.
- **Activity.** Grants now appear in the feed as `kind: "grant"` events (`source: "admin"`, severity low),
  written when an admin grants access or approves a grant request. `department=` and `team=` filter the feed.
- **Cache.** Whole response bodies of the aggregate GETs (`org`, `org/teams`, `org/unit`, `people`,
  `outliers`, `incidents/summary`, `timeseries`, `adherence`, `usage`, `value`, `overview`, `catalog`, `menu`,
  `principals`) are cached per query string, with an `x-cache: hit|miss` header. Besides admin POSTs, a policy
  reload (an automatic tighten or quarantine) and a newly opened incident also clear it. The activity feed,
  incident list and leases are never cached.
- **`/admin/principals`** keeps its array shape, capped at 200. `demo/live.py` now reads `/admin/people?q=`.
