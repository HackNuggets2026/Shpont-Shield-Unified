# API notes from the Security pages

Found while building `/console/security` and `/console/incidents/:id` against a seeded gateway
(`seed --days 30 --seed 42`). Each item says what the SPA does about it today.

1. **`GET /api/admin/incidents` only lists the last 7 days.** It reads `RiskEngine.incidents`, which is loaded
   with `since=now-7d`. Five of the 11 seeded incidents (ivan, mallory, judy, carol, erin) are missing, and so
   are `GET /api/admin/people/{pid}` → `incidents`. They are still in SQLite, and `GET /api/admin/incidents/{id}`
   returns them.
   *Workaround:* `lib/security.ts#incidentsWithHistory` reads `GET /api/admin/activity?source=detections&kind=incident`,
   fetches each unknown id by id, and marks them "archived · not scored".
   *Suggested fix:* a `since`/`days` param on `/admin/incidents` that falls back to `usage.incidents()`.

2. **Live status changes are missing from the incident's `actions`.** `POST /api/admin/incidents/{id}` logs
   `incident_<status>` with `target = <incident id>`, but `GET /api/admin/incidents/{id}` filters `actions` by
   `target = <principal>`. The seeded `incident_*` actions use the principal as target, so seeded data looks
   fine but a live acknowledge or resolve doesn't show up.
   *Workaround:* the incident page also reads `GET /api/admin/actions?target=<incident id>` and merges the two.
   *Suggested fix:* include actions whose target is the incident id. Also pick one target convention for `incident_*`.

3. **A detection's own event is flagged `evidence: true`.** The `detections/incident` event sets
   `request_id = evidence[-1]`, so in the timeline it matches the evidence ids. Other incidents that share that
   evidence id are flagged too.
   *Workaround:* the SPA ignores the `evidence` flag on `source == "detections"` and shows those rows as
   "Incident raised" markers instead.

4. **"View content" is empty for seeded history.** `GET /api/admin/principals/{pid}/events` reads the in-memory
   audit buffer, so after a restart frank's exfiltration has no content. Live traffic works: `demo/live.py`
   creates content for carol. Blocked inputs come back with `text: null` (content not stored).
   *Workaround:* the dialog explains this in its empty state. The evidence timeline already shows each block reason
   (`detail.reason`) from the activity log.

5. **Detection thresholds only come from the full policy dump.** The SPA reads `GET /api/admin/policy` →
   `policy.detections.response` (alert/tighten/quarantine) and `half_life_minutes`. That response is fairly large,
   so it's cached for 60 s. A small `GET /api/admin/detections` returning `{levels, thresholds, half_life, rules}`
   would be cleaner.

6. **Notes are optional server-side.** `POST /api/admin/incidents/{id}` accepts an empty `note`. The UI requires one
   for resolve and dismiss but not for acknowledge or reopen. The content view relies on the server's 400 for a
   missing reason, and the UI shows that message.
