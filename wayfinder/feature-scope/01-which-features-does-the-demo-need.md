# Which features does the demo need?

Labels: wayfinder:prototype · HITL
Parent: [Feature scope for the demo](map.md)
Blocked by: nothing
Assignee: Mikołaj
Status: closed

## Question

Which of the 62 features in the fork do we keep, and which need explaining first? Nothing starts kept on the [feature scope page](https://claude.ai/artifact/FnPwnSpKAt1Q88A7LiNAkC), grouped into Enforce, Measure, Detect and respond, Show, and Demo; Claude marks three features it would drop.

## Resolution

Reviewed on the feature scope page, then decided in chat (2026-10-03):

- Keep: Enforce (gateway and guardrails, Claude Code), Measure (usage and cost, resources, events, organisation), Detect and respond (security and risk: beat 5 of the demo), Show as the admin console only, Demo and tooling.
- Ditch: the employee portal UI (`web/src/pages/portal`, `components/portal`; employee keys can no longer sign in to the console, `/api/me/*` stays), the old HTML dashboards (`controllayer/dashboard`), and the layout drafts `draft/ops-cockpit` (3d65c73) and `draft/workflow-first` (860b2b6), deleted from GitHub; local branches `var-b` and `var-c` still hold them.
- Done in e67d80b.
