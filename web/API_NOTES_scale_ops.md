# API notes from the scale-ops pages (Security, Incident, Resources, Requests)

Built against `docs/scale-contract.md`. The typed client is `src/opsApi.ts`. Each item lists what the SPA sends or
expects beyond the contract, and what it does when the server doesn't do it.

## Incidents
1. **"Needs attention" is two statuses.** The default view sends `status=open,acknowledged`, and the contract only
   specifies a single status. *Ask:* accept a comma list (or `status=active`).
2. **`severity`, `team` and `q` filters** on `GET /admin/incidents`. The contract lists only `department`, `rule`, `status`
   and `principal`, but the table filters on severity, team and person (`q` matches id, name or email like `/admin/people`).
   The "Open incidents" KPI also counts `status=open&severity=<s>&limit=1` for each severity.
3. **Severity-first order.** The SPA sends `sort=severity`, meaning high, then medium, then low, then open before
   acknowledged, then newest. The rows on each page are re-sorted, but the order across pages has to come from the server.
4. **Rule × department matrix.** `GET /admin/incidents/summary` has `by_rule` and `by_department` but not both together.
   The heatmap reads `by_rule_department: [{rule, department, open, total}]` when it is there. Otherwise it falls back to
   `GET /admin/incidents?days=30&limit=5000` and aggregates in the browser, which is fine at about 150 incidents in 30
   days but not at 50k employees. *Ask:* add `by_rule_department`. `by_severity: {high, medium, low}` (open only) would
   also save the three count calls.
5. **Restricted counts** come from `GET /admin/people?status=quarantined|revoked|limited&limit=1` → `total`.

## Leases
6. `GET /admin/leases` takes `limit` but no `offset`, so the open-leases table can't page. The SPA sends
   `offset`. It also sends `zombies=1` for "zombies only", which is not in the contract.
   *Fallback:* rows on the current page are filtered in the browser, so a wrong row never shows up.
7. When `summary` is missing, the rollups are computed from `open` (only correct while `open` is unpaged).

## Grants
8. The envelope key for `?envelope=1` is not named in the contract. The client accepts `grants`, `rows` or `items`.
9. The SPA sends `live=1` together with `envelope=1`, `department`, `resource`, `limit` and `offset`.
   `summary` is assumed to be org-wide, so it ignores the filters.

## Requests
10. **No `total`** on `GET /admin/requests`. Paging falls back to "has more", meaning a full page came back. The
    "Waiting" KPI uses `overview.requests_pending` when no filter is set. *Ask:* return `{total, rows}` (for example
    behind `?envelope=1`) or a `total` header.
11. **Oldest first.** The pending queue wants the oldest first, but the server returns newest first. Each page is
    re-sorted, so with more than 50 pending the oldest ones end up on the last page. *Ask:* `order=asc`.
12. **"All decided" history.** There is no status that means approved or denied, so "All" in History sends no
    status and drops pending rows in the browser. Those pages can come up a few rows short. *Ask:* `status=decided`.
13. **Bulk decisions** are N sequential `POST /admin/requests/{id}` calls, each with the same note. That works,
    but a `POST /admin/requests/bulk {ids, decision, note}` would make it atomic and leave one admin-log entry.

## Activity
14. `interesting=1` is used for the Security signal feed, with no other client-side filtering.

## Usage
15. Spend by department uses `GET /admin/usage?by=department,resource&days=30`. If that returns an error it falls
    back to `by=department` alone.
