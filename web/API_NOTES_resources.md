# API notes from the Resources / Requests pages

Found while building `web/src/pages/console/Resources.tsx` and `Requests.tsx` against a seeded gateway
(`seed --days 30 --seed 42`). The SPA works around each one; nothing here blocks the demo.

1. **`catalog[].workflows` only counts `workflow.resources`.** A workflow that allows a resource's model (e.g.
   `chat_assist` allows `gpt-4o-mini`, `llama3.2:*`, `mock-*`) or tool is not listed. The SPA adds those
   client-side by glob-matching `menu.workflows[].models/tools` against `catalog[].models/tools` and marks them
   "·model" / "·tool". Suggest the server do the same in `admin_catalog` (`controllayer/gateway/catalog.py`).
2. **`owner` is empty for every consumable and leasable entry** in the seeded policy (only the access_grant
   entries have one). The UI shows "unassigned". Filling `owner:` in `policy.yaml: catalog` would make the
   catalog and the Backstage export (`spec.owner: unknown`) read better.
3. **Zombie window is very short.** `sweep()` flags a lease `idle` and `auto_reclaim` stops it in the same
   10 s pass, so the `flags: ["idle"]` state is visible for at most one poll. The UI therefore shows idle time
   against the resource's `lease.idle_minutes` ("zombie in 3.5 min") before the flag, and lists recent
   `end_reason: "reclaimed: …"` leases under the running table so the reclaim does not vanish silently.
   A grace period (flag, then reclaim N minutes later) would demo better if wanted.
4. **`POST /admin/principals/{pid}/grants` does not check that `pid` exists**; a typo creates a grant for a
   principal with no key. The UI only offers known people.
5. **Grant `minutes` above `grant.max_minutes` are silently capped** (both direct grants and approved grant
   requests). The UI states the cap ("max 240 min" in the dialog, "capped at …" on a request) instead of
   surfacing an error.
6. **Approving a quota request sets `budget_scale` outright**, including for a quarantined person (seeded
   carol after the live demo: 10% → 200%). The Requests page now shows the person's status pill next to the
   request so an admin sees that before approving.
7. **`GET /admin/catalog` usage for `meter: billing_export` has only `usd`** (no tokens/minutes/requests), so
   the volume column says "imported from the bill".
8. `GET /admin/grants` returns expired grants for 7 days (kept on purpose by `new_grant`); the page defaults to
   live only with an "Include expired" toggle.
9. Exports need the admin header; `downloadExport` in `api.ts` fetches with it and saves a blob. The
   `?token=` query form also works but puts the token in the URL, so the SPA does not use it.
