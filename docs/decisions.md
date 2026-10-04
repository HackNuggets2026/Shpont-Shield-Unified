# Product decisions

## Employee panel (`/me`): dropped

**Status:** decided. The admin console at `/` is the product; there is no employee screen. Shield's old employee panel survives only as a read-only reference under `/legacy/me`.

**Why:** a company adopts this more easily when ordinary employees never have to learn or touch anything. The control layer should be invisible to them: their tools keep working, and only the security team operates it. A panel every employee must use adds:
- training and change-management cost to the rollout,
- more surface to support and secure,
- friction that works against the "drop-in, nobody notices" pitch.

**What we would keep:** the capabilities behind `/me` (agent resource grants, usage, activity), moved under the security console or an admin API:
- security staff, or an automated policy, assign resources to agents;
- employees are told about the monitoring through the company's acceptable-use policy, not through a UI.

**Outcome:** the employee UI is gone. The JSON behind it stays for employees' own tools (`/api/me/*`, `/me/api/*`, with their own key), and new work targets the admin console only.
