# API notes from the scale-ops pages (Security, Incident, Resources, Requests)

Checked against `scale-backend` M2 (`4f1adf4`) on the 5k-person seed (`seed --days 30 --seed 42`). The typed client is
`src/opsApi.ts`. Each item says what the SPA needs, what M2 does, and what the SPA does about the difference.
Params the server doesn't know are still sent, so a server fix needs no client change.

## Incidents (`GET /admin/incidents`)
1. **"Needs attention" means two statuses.** The SPA sends `status=open,acknowledged`, but M2 only matches a single status.
2. **Severity, team and person search filters are missing.** The SPA sends `severity`, `team` and `q` (id or name).
3. **No severity-first order.** The SPA sends `sort=severity`: high, then medium, then low, then open before acknowledged,
   then newest. M2 always returns newest first.
   *What the SPA does for 1–3:* when any of them is in play (always true by default, since the order is severity-first),
   it asks for `limit=5000` with the filters the server does support, then filters, sorts and pages in the browser.
   That is correct, and it's cheap at about 180 incidents per 30 days, but it won't hold at 50k employees with years of
   history. *Ask:* a comma list (or `active`) for `status`, plus `severity`, `team`, `q` and `sort=severity`.
4. **The rule × department heatmap has no endpoint.** The summary has `by_rule` and `by_department`, but not the two
   combined. The heatmap reads `by_rule_department: [{rule, department, open, total}]` when it is present. Otherwise it
   aggregates `GET /admin/incidents?days=30&limit=5000` in the browser. *Ask:* add `by_rule_department`. Also add
   `by_severity` (open only), which would save the three count queries behind the "Open incidents" KPI.
5. **`by_department.open` counts `open` only, not acknowledged**, while `people_at_risk` counts both. The heatmap's
   "Needs attention" mode counts open plus acknowledged. The two views can differ by the acknowledged count, and the
   labels say which is which.
6. **`GET /admin/incidents/{id}` lacks `department`, `team` and `name`.** Only list rows carry them. The incident page
   looks the person up with `GET /admin/people?q=<pid>`. *Ask:* add `who_fields` to the detail endpoint
   (`incident` and `principal`).
7. **`GET /admin/people/{pid}` has no `department` or `name`**, and the role of a directory-only person comes back as
   `"?"`. The SPA hides that `"?"`.

## Leases (`GET /admin/leases`)
8. **No `offset`, and no zombies filter.** M2 takes `limit` but not `offset` or `zombies`.
   *What the SPA does:* it asks for `limit=(page+1)*25` and cuts out the page, and for zombies only it asks for
   `limit=1000` and filters in the browser. It still sends `offset` and `zombies=1`.
   `summary` is org-wide whatever the filters, and the UI relies on that.

## Grants (`GET /admin/grants?envelope=1`)
9. This matches the contract: `{total, grants, summary}`, with filters and paging. No gap. The client also accepts
   `rows` and `items` as the array key.

## Requests (`GET /admin/requests`)
10. **No `total`.** The array shape stays. Paging falls back to "has more", meaning a full page came back, and the
    "Waiting" KPI uses `overview.requests_pending` when no filter is set. *Ask:* `{total, rows}` behind `?envelope=1`.
11. **Oldest first.** The queue wants the oldest first, but M2 returns newest first. Each page is re-sorted, so past
    50 pending the oldest are on the last page. *Ask:* `order=asc`.
12. **No "decided" status.** History's "All" view asks for `limit = page rows + pending count` with no status and
    drops pending rows in the browser. *Ask:* `status=decided`, or a comma list.
13. **Bulk decisions are N sequential calls** to `POST /admin/requests/{id}`, each with the same note. The SPA reports
    which ones succeeded and which failed, and keeps the failed ones selected. *Ask:*
    `POST /admin/requests/bulk {ids, decision, note}` for one atomic call and one admin-log entry.
14. **Seed data: some decided requests have `decided_at` in the future**, so the UI shows "in 17h" or "in 1d"
    (James Jensen, Ben Thompson and others on the 5k seed). This is a seeder bug, and the UI doesn't hide it.

## Activity, usage
15. `GET /admin/activity?interesting=1` feeds "Security signal" as is.
16. Spend by department uses `GET /admin/usage?by=department,resource&days=30`. M2 supports it.
