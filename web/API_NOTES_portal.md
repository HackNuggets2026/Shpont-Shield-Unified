# API notes from the employee portal

Things the portal (`web/src/pages/portal/*`) works around. Each has a UI workaround in place today; a small API change would remove it.

1. **Quarantine effects are not in `/api/me/summary`.** The portal says "read and search tools still work, 10% budget,
   grants suspended" based on `policy.quarantine` and `controls/access.py` / `controls/resources.py`. Exposing
   `status.quarantine = {tools, budget_scale}` would let the banner list the actual tool patterns.
   *Workaround:* fixed copy plus `status.budget_scale` from the API.

2. **Risk thresholds and decay are not exposed.** Employees see the score but not where alert / tighten / quarantine
   start, or the half-life. *Workaround:* the thresholds are parsed from `status.reason` ("risk score 118.9 reached
   quarantine (80)") and the copy says "halves every couple of hours". Suggest
   `risk.levels = {alert, tighten, quarantine}` and `risk.half_life_minutes`.

3. **Admin-action `detail` has two shapes.** Detections log the flat patch (`{status: "quarantined"}`); admin writes
   (`_write` in governance.py, `write` in catalog.py) log the whole overlay patch (`{principals: {pid: {...}}}`), and
   grants carry the full grant list. *Workaround:* `actionPatch()` in `components/portal/explain.ts` unwraps both.
   The shared `AdminLog.actionLabel` only reads the flat shape, so live admin restrictions show as a generic "Restrict"
   in the console.

4. **Incident status changes are logged with `target = incident id`**, not the principal
   (`governance.py` `admin_incident_edit`), so live "resolved / dismissed" actions never reach the employee's
   `admin_activity`. The seed writes them with the principal as target, so seeded data looks right.
   *Workaround:* none on the portal; the incident's status and note still show under Privacy, then Risk.

5. **Denying a request is not logged as an admin action.** On deny, `admin_request_decide` only calls `usage.decide_request`
   (no `log_admin`), so a denial shows on the request (status, decided_by, note) but not in the "every change" record.
   *Workaround:* the request list shows the decision and the note.

6. **`/api/me/activity` has no `source` / `kind` / `since` filter and caps at 500 rows.** Claude Code sessions,
   "blocked or changed" and the 30-day KPIs need 30 days of events. *Workaround:* `useMyEvents(30)` pages back with
   `before` (up to 12 pages, 6000 events) and marks the result partial past that. A `source=` filter or a
   `/api/me/claude_code` aggregate (cost, lines, commits, PRs by session) would make this one call.

7. **`summary.events` only holds the in-memory audit ring**, so it is empty after a seed or restart. *Workaround:* the
   blocked list is built from `/api/me/activity` (`check.*` with decision block / redact / warn), which persists.

8. **`summary.collected` does not say what is *not* collected.** The portal adds a fixed list (blocked prompt text,
   Claude Code prompt text and tool parameters, unmasked PII/secrets, anything outside AI calls), taken from
   `claude_code.py` `DROP_ATTRS` and `audit.store_raw_text: false`. If a deployment turns `store_raw_text` on, that
   list would be wrong; consider returning `not_collected` from the API.

9. **Policy reasons are machine strings** (`access_grant/grant_required: ...`, `harmful_request/malware: malware p=0.90
   conf=0.80`). *Workaround:* `explainReason()` maps categories to plain language and keeps the code for reference.
   New control categories fall back to a title-cased code.

10. **Budget windows.** `budgets` are today's (UTC) scopes only; the month has no limit, so the portal shows
    month-to-date with a simple pace estimate, not a bar.
