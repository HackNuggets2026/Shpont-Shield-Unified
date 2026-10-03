# Feature scope for the demo

Labels: wayfinder:map

## Destination

Every feature in shpont-shield-mikolaj is marked Now, Later or Cut, so later sessions build and polish only what the HackYeah demo needs, and know what to delete or hide.

## Notes

- Domain: the Goldman Sachs "AI control layer" brief; 3-minute demo driven by `demo/live.py` on a seeded 5,000-person bank.
- Review tool: [Shpont Shield feature scope](https://claude.ai/artifact/FnPwnSpKAt1Q88A7LiNAkC). Picks are stored in its `decisions` collection; read them with ArtifactData before resolving tickets.
- Teammates cover parts of the space: Shpont-Shield (base guardrails), Shpont-Shield-Redteam (attacks), Shpont-Shield-Behavior (behaviour judging), SzpontShield (prompt checker). Prefer cutting what they already own.
- Data shapes follow HackNuggets2026/Common-Database (pull first; teammates edit it too).
- Tracker: local markdown in this folder (no tracker configured; `/setup-matt-pocock-skills` would set one up).

## Decisions so far

## Not yet specified

- What to do with each Cut feature: delete the code, hide it in the UI, or leave it dormant. Depends on the review.
- Whether the employee portal shrinks to Home and Menu only.
- Which usage detections the story needs; the rest may be trimmed once the Now set is known.

## Out of scope

- New features. This map only decides what to keep among what exists.
