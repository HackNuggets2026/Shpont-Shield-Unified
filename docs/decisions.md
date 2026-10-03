# Product decisions

## Employee panel (`/me`): likely to be dropped

**Status:** under consideration, leaning towards removal. The security console is the product.

**Why:** a company adopts this more easily when ordinary employees never have to learn or touch anything. The control layer should be invisible to them: their tools keep working, and only the security team operates it. A panel every employee must use adds:
- training and change-management cost to the rollout,
- more surface to support and secure,
- friction that works against the "drop-in, nobody notices" pitch.

**What we would keep:** the capabilities behind `/me` (agent resource grants, usage, activity), moved under the security console or an admin API:
- security staff, or an automated policy, assign resources to agents;
- employees are told about the monitoring through the company's acceptable-use policy, not through a UI.

**Until decided:** `/me` stays as it is. New work targets the security console first and does not add employee-facing features.
