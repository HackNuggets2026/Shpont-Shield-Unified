# API notes (found while building the SPA)

Each note says what the UI does about it today.

1. **Content views and `/me/summary.events` read only the in-memory audit ring.** Both `GET /admin/principals/{pid}/events` and `/me/summary.events` read `layer.audit.events`. That ring is empty after a restart and for seeded history, so "View events" (a reason is required) can return nothing even for a person with thousands of events.
   - UI: the portal's "Blocked and flagged" list is built from `/me/activity` instead (check events whose decision is block, redact or warn).
   - Suggestion: back both endpoints with the events store.
2. **`/me/summary.menu[].available` ignores the account status.** A quarantined person sees workflows marked "available".
   - UI: the portal overrides this. When status is not `active`, every workflow shows as paused.
3. **`/me/summary.runs` is capped at 30 rows.**
   - UI: the "Tasks this week" tile shows "30+" when it hits the cap.
4. **`/me/activity` has no `source` / `kind` / `severity` filters.** The admin feed has them.
   - UI: the portal filters on the client, over the newest page plus "load older". Claude Code sessions are computed from the newest 500 events.
5. **There is no aggregate for `metric.*` values** (commits, lines of code, pull requests).
   - UI: cost per commit, cost per PR, lines per $1 (Overview) and AI $ per commit by workflow (Workflows) page through `/admin/activity?kind=metric.commit|metric.pull_request|metric.lines_of_code`, 500 rows at a time, up to 10k events.
   - Suggestion: support `metric=value` in `/admin/timeseries`, or add an `/admin/output?by=workflow` endpoint.
6. **`/admin/activity` has no `decision` filter.** "Only blocked" therefore cannot be asked for server-side.
   - UI: Security uses `severity=high` instead.
7. **The incident detail's `principal` has no `budget_scale`.**
   - UI: the incident page reads it from `/admin/principals`, so that "Restore" appears for a person who is tightened but active.
8. **`/admin/people/{pid}` returns `leases` as a list, while `principal_row.leases` is a count.** The person view overwrites the count. The types model the list.
9. **`/admin/people/{pid}.grants` rows lack `title`, `principal` and `minutes_left`.** `/admin/grants` rows have them.
   - UI: fills in `principal` and falls back to the resource name.
10. **`/admin/incidents` lists only the in-memory `risk.incidents`.** With the seeded data that is 6 incidents, while the activity stream holds 11 `incident` events. `/admin/incidents/{id}` already falls back to the store, but the list does not.
11. **Admin exports need the admin token.** It is sent in a header, so they cannot be plain links.
    - UI: downloads them with `fetch`, then saves the blob. A `?token=` query also works, but it would put the token in browser history.
12. **Login probe noise.** The login probes `/api/session` as admin first, so an employee login logs one expected 401 in the browser console.
