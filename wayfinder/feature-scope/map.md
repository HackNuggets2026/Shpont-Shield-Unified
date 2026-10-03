# Feature scope for the demo

Labels: wayfinder:map

## Destination

Every feature in shpont-shield-mikolaj is either kept (its category, area and the feature itself ticked Keep) or dropped (anything left unticked), and everything marked Explain has been explained, so later sessions know what to delete or hide before the HackYeah demo.

## Notes

- Domain: the Goldman Sachs "AI control layer" brief; 3-minute demo driven by `demo/live.py` on a seeded 5,000-person bank.
- Review tool: [Shpont Shield feature scope](https://claude.ai/artifact/FnPwnSpKAt1Q88A7LiNAkC). Picks are stored in its `nodes` collection (one doc per category, area or feature: keep, explain); read them with ArtifactData before resolving tickets.
- Teammates cover parts of the space: Shpont-Shield (base guardrails), Shpont-Shield-Redteam (attacks), Shpont-Shield-Behavior (behaviour judging), SzpontShield (prompt checker). Prefer cutting what they already own.
- Data shapes follow HackNuggets2026/Common-Database (pull first; teammates edit it too).
- Tracker: local markdown in this folder (no tracker configured; `/setup-matt-pocock-skills` would set one up).

## Decisions so far

- [Which features does the demo need?](01-which-features-does-the-demo-need.md): keep Enforce, Measure, Detect and respond, the admin console and Demo and tooling; ditch the employee portal UI, the old HTML dashboards and the two layout draft branches (done in e67d80b).

## Not yet specified

- Which usage detections the story needs; the rest may be trimmed now that Detect and respond is kept.
- Whether the employee API (`/api/me/*`) should go too, now that no screen uses it.
- Features inside kept areas that might still go (Backstage export, `lib/productivity.ts`, CloudEvents ingest): decide at feature level only if they get in the way.

## Out of scope

- New features. This map only decides what to keep among what exists.
