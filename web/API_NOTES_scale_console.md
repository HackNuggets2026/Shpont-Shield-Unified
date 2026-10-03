# API notes from the scale console (Overview, Organization, unit pages, People search, Workflows)

Found while building the enterprise-scale console against `docs/scale-contract.md`. The SPA works around
each one; none blocks the demo.

1. **`GET /admin/people/{pid}` has no `department` or `name`.** The person page needs both for its
   breadcrumbs (Organization › department › team › person). The SPA runs one extra
   `GET /admin/people?q=<pid>&limit=10` and picks the exact `principal` match. Suggest adding
   `department`, `name` (and `title`, `location`) to the person detail.
2. **`/outliers` growth rows have no `name`.** `cost` and `risk` rows carry `name`; `growth` rows only have
   `principal`. The UI falls back to the id. Suggest adding `name` for consistency.
3. **`/org/teams` has no name filter (`q=`).** The global search box matches team names client-side against
   one `?limit=200` page. Fine at 41 teams, not at a 50k-person install with ~1,000 teams. Suggest `q=` on
   `/org/teams` (prefix/substring, like `/people`).
4. **`/org/teams` sorts only by `usd|usd_per_active|adherence|risk|headcount`.** The teams table therefore
   offers only those columns as sortable (name, Δ, interventions, incidents are not). Suggest `name` and
   `growth` (usd vs usd_prev).
5. **`/activity` cannot be filtered by `department` or `team`.** Rows gain `department`, but there is no
   filter parameter, so department and team pages cannot show a scoped feed. The console keeps the feed on
   its own page (`/console/activity`, `interesting=1` by default).
6. **No previous-window values for rates.** `usd_prev` exists, but adherence, people at risk and
   interventions have no previous value, so only spend shows a Δ. Suggest `adherence_prev` (and
   `interventions_prev`) on the org totals and unit rows.
7. **Per-workflow previous period.** The Workflows page derives "previous 30 days" per workflow as
   `usage?by=workflow&days=60` minus `days=30`. A `usd_prev` on `usage` rows (or a `prev=1` flag) would
   save the second query and the subtraction.
8. **Budget is daily only.** The "forecast vs budget" tile multiplies `global_usd_per_day` by the days in
   the month. A monthly org budget (and per-department budgets) in `org` would make that tile exact and
   allow a budget column per department.
9. **`/org/unit` spend is by workflow only.** That is now the main chart on unit pages, which is what the
   product wants; a `by=` on the unit endpoint (department → team split) would let a department page show
   which team drives a workflow without a second query.
10. **Workflows have no display title.** `menu.workflows[]` has `name` and `description` only, so the SPA
    keeps a small label map (`web/src/lib/workflows.tsx`: "bugfix" → "Bug fixing"). Suggest an optional
    `title` on workflows in `policy.yaml`, served by `/admin/menu`.
11. **Claude Code $/commit needs `value` by department.** The Overview sums
    `GET /admin/value?by=department` rows (`claude_code_usd / commits`). The old client-side approach
    (paging `/activity?kind=metric.commit`, capped at 10k events) does not survive the rollup seed; the
    Workflows page now uses `value?by=workflow` as well.
