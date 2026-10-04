# Architecture

## Request lifecycle

Every interaction becomes a `Context`: principal, direction, text, model or tool. It goes through one pipeline (`controllayer/engine.py`):

| Stage | What | Cost | Short-circuits |
|---|---|---|---|
| Gates | API key → principal, model allowlist, tool RBAC, irreversible tools (blocked) | µs | yes |
| Budgets | requests/min, tokens/day, USD/day per principal, team and global; identical-call loop guard | µs | yes (429) |
| Deterministic | secrets, PII (with Luhn/IBAN/PESEL checksums), attack-signature feed | <1 ms | block skips the model call |
| Semantic tier 1 | `tev1:0.8b` answers every applicable policy question for every chunk | model | - |
| Semantic tier 2 | `nimble` re-answers only the questions tier 1 put in `escalate_band` or below `min_confidence` | model | - |
| Decide | strongest action wins; redact spans or withhold the whole text; audit event | µs | - |

Structured payloads (tool arguments, tool results, non-text message fields) are flattened to raw text for inspection, with JSON-in-strings decoded. Redaction is applied to every key and string in place and then re-checked. JSON inside a string is redacted value by value and re-serialised, so a span cannot swallow the JSON punctuation around it. If anything is still detected (a card number stored as an integer, a secret split across fields), the payload is refused, never forwarded.

The directions are `input`, `output`, `tool_call`, `tool_result` and `tool_description`. Each control declares which directions it inspects. The chat proxy checks every message in the request on the way in, because the client owns the history and can forge it; repeats are served from a verdict cache. It checks the completion on the way out. `/v1/messages` does the same per content block: each text, thinking and `tool_result` block is its own check (so a placeholder lands in the right block), every other block and key is checked together, and each reply block is checked before it is released, mid-stream included. The MCP proxy checks arguments, every result and the tool list itself, including names and schema strings, so a poisoned tool is removed before the agent ever sees it.

## Identities, resources and grants

API keys map to principals of kind `human` or `agent`, and every agent has an owner. The resource catalog (`resources:` in the policy) is security-owned and lists who is entitled to each resource. Grants are runtime state in `data/state.json`, written atomically and changed only through the admin API and console, or the employee's own `/me/api/*` calls. Security edits the policy; grants are delegated per agent.

`identity.demo_mode` removes every credential check, for demos only; it must be off in production. The admin token is skipped. A gateway caller (chat, `/v1/messages`, MCP, `/v1/guard`) without a known key acts as `identity.demo_principal`, a human the loader requires to exist, so its traffic still lands on a real person's budgets, risk score and audit trail. A known key still names its own principal. The `/me/api/*` endpoints act for whoever `x-acl-as` names, the demo principal by default. A risk signal without an integration token names a configured integration in its body and gets that integration's caps. The gateway logs a warning whenever it loads such a policy, and `/admin/summary` carries `demo_mode`.

Each catalog entry of `type: service` names one service in `controllayer/services.py` (`connection.service`) and the environment variable holding its credential (`connection.secret_env`). A service is catalogued at most once, so a tool name identifies the resource it uses. Each tool declares the scope it needs, and the catalog's `scopes` must be a subset of the scopes the service's tools use: a scope left out keeps its tools from every grant. Two scopes unlock no tool: `pii` is accepted on services with masked columns, and `external_share` on services whose egress tools address people. The policy loader rejects an unknown service, a scope the service cannot use and a service catalogued twice. `scope_entitlements` limits single scopes to some of the people entitled to the resource. A person can use and delegate only the scopes they are entitled to, and a live grant loses a scope as soon as its owner does.

A broker call has a resource and a scope, both given by the tool. It passes the gate only if all of these hold at that moment:
- the agent holds a grant with that scope;
- the grant has not expired;
- the owner is still entitled to the resource and to that scope;
- the resource is not suspended;
- for an egress tool, every recipient is in a `company_domains` domain, unless the caller holds `external_share`. Any other string, such as a list or a display name, counts as outside.

Such decisions are never cached, because expiry depends on time. `tools/list` applies the same rules, so it shows only the tools the caller can use now. The tool's arguments are validated against its schema (unknown keys, types, enums, ranges) before the backend runs. SQL tools accept one `SELECT` or `WITH ... SELECT` statement with no write, DDL or session keyword outside literals, quoted names and comments, and run it on a read-only database with a statement timeout and a cap on value and result size. Blobs come back as bytea hex (`\x41`). Without the `pii` scope, the query runs on a copy of the data in which the columns classified in `services.yaml` are already masked. Post-hoc redaction alone cannot stop `hex()` or `substr()` from reshaping a value past the detectors. Before an egress tool runs, the object it sends and its message are checked as both a tool call and a tool result, and the call is refused if any of it would be redacted, withheld or blocked. A grant's expiry is set once, when it is created or when an expired grant is renewed. Changing its scopes (`PATCH /me/api/grants/{agent}/{resource}`) keeps the expiry and works only on an active grant. A new grant over a live one is refused. Reaching a catalogued MCP server needs a grant too, but per-tool RBAC and the irreversible-tool rule still apply on that server. Agents cannot reach uncatalogued servers at all.

## Insider risk

`risk.RiskEngine` (`layer.risk`) keeps a decaying score per principal. Points come from each finding's *proposed* action, so shadowed and capped findings still count as intent, plus category weights. Rate limits, outages and auth failures are not evidence. A person's own level is the one security set manually if any (an override, in either direction). Otherwise it is the higher of the score-based level and the strongest unexpired external signal. The effective level is the higher of that and, for agents, the owner's own level. External signals (`POST /admin/risk/{pid}/signal`) are stored per source in `data/state.json` with their expiry. Expired ones are ignored on read and dropped on the next write. Each read also checks them against the current policy: a signal counts only while its integration is listed, and is capped to that integration's present `max_level` and `max_ttl_hours` (counted from when it was sent). An integration holds at most `max_sources` live sources per principal, so one token cannot grow the state file without bound; a new source past the cap evicts the weakest, soonest-expiring one, never a stronger one. The endpoint is the one `/admin` path that skips the admin token; outside demo mode it accepts only a token from `identity.integrations`, never one equal to the admin token. A rise of the auto level while an override holds the person raises an alert, so security sees the drift. The level selects the policy variant (`watch_controls`), turns on raw capture, or blocks (`restricted`). It is part of the verdict cache key.

## SIEM export

`controllayer/export.py` maps native records to OCSF 1.9.0 (checked against schema.ocsf.io; `tests/ocsf_1.9.0_schema.json` is the extract the tests validate against) and to ECS 9.5:

| Record | OCSF class | ECS |
|---|---|---|
| Decision | API Activity (6003), profiles `security_control` + `ai_operation`: `action_id`/`disposition_id` from the action, `message_context.prompt_text` or `response_text` (by whether the text goes to the model or comes from it), `ai_model` | `event.kind: event`, `event.category: api`, `gen_ai.*` |
| Grant / revoke | User Management (3007), Assign / Remove Privileges; `user` is the agent, `privileges` the scopes | `event.category: iam` |
| Risk level, suspension | Entity Management (3004), Update / Suspend / Resume | `iam` / `configuration` |
| Insider-risk alert | Detection Finding (2004): `risk_score`, `risk_level`, `finding_info`, `evidences` | `event.kind: alert`, `event.risk_score` |

An agent's event names its owner as the user (`actor.user`, ECS `user`), the agent as `ai_agent` (ECS `gen_ai.agent`) and the owner-to-agent binding as `delegation`. Fields OCSF has no place for (findings, channel, latency, `raw_text`) go in `unmapped` (ECS: `controllayer.*`). `src_endpoint.ip` / `source.ip` is the HTTP peer of the request. Every mapped text is the masked native one.

## Contextual PII

The Privacy Filter sidecar returns BIOES-decoded spans. The gateway applies `min_score`, maps labels to actions and merges the spans with regex findings. In chat, reversible labels become placeholders numbered per request, and are put back after the reply passes its own checks. PII values the caller supplied in the same request are not re-redacted in the reply. Before the request leaves, every masked value is replaced by its placeholder wherever else it occurs (base64-like strings excepted), because a reply restored for the caller comes back as history where the detector may not find it again. Numbering follows the order of first appearance, so an unchanged conversation prefix is masked identically on every turn and prompt caching keeps working. Overrides (user header or decision model) downgrade PII findings to `log`, never past `override_max`, and are written to the audit trail.

## Why decision models

Ollama's `/v1/systemone` takes text plus up to 64 typed questions and returns a probability per answer. That fits the brief's "Block vs Redact or adherence %" requirement directly:

- A semantic control is a question plus thresholds in YAML, so judges can add one live.
- The probability maps to the action ladder with no prompt parsing and no free-text verdicts to jailbreak.
- `confidence` drives escalation: the cheap model handles the clear cases and the 9B model only sees the ambiguous ones.

Content is sent as a JSON field (`{"content_role": ..., "content": ...}`), so instructions inside it read as data rather than as the judge's task. Ollama sums `usage.input_tokens` across questions, which implies one pass per question. So each control only asks the questions that apply to the current direction.

Ollama rejects oversized requests (64 KiB body; tev1 is best under about 2k tokens) instead of truncating them. Long tool outputs are therefore split into overlapping chunks, and the worst answer across chunks wins. An injection buried 24 KB into a document is still found. Chunks overlap by up to 200 characters, so a phrase that short is never split (`tests/test_semantic.py`).

If the model is down or times out, `fail_mode: closed` blocks and `open` allows with a `log` finding. The outcome is explicit either way.

## Policy model

- Single YAML file, strict schema: a misspelled key is an error, not a silently disabled control.
- The version is the hash of the policy after env expansion. It is stamped on every audit event, so you can always tell which policy made a decision.
- Polled every second. An invalid edit keeps the previous policy live and surfaces the error.
- `${VAR:-default}` expansion keeps secrets and per-deployment URLs out of the file.
- `mode` caps a control's action. `shadow` evaluates it without enforcing. Team overrides deep-merge.

## Budgets

Commercial models are priced per 1M input/output tokens; Anthropic prompt-cache writes and reads count as input tokens priced at 1.25x and 0.1x unless the policy prices them. Local models are priced per compute-second, using measured upstream latency times `usd_per_compute_second`. Company-service calls are charged per call (`budgets.services`, with per-tool prices) against the same budgets. Limits apply per principal (`per_principal`, overridden field by field per person in `per_person`), per team and globally per UTC day. Every charge also lands in the console's usage history (daily buckets for 62 days, hourly for 48 hours, per principal and model or service; in memory, seeded from `data/history.json`). The loop guard stops an agent that repeats the same call more than N times in a window.

## OWASP mapping

| OWASP LLM Top 10 (2025) | Control |
|---|---|
| LLM01 Prompt injection | `prompt_injection` semantic control on input, tool_result and tool_description; jailbreak signatures; tool-poisoning signature |
| LLM02 Sensitive information disclosure | secrets + PII detectors in both directions; `confidential_output`; `data_exfiltration` |
| LLM03 Supply chain | signature feed: pickle opcodes, `weights_only=False`, `trust_remote_code` (blocked on input and tool calls), CVE-2025-32434 (`torch.load` before 2.6), CVE-2024-3660 (Keras Lambda, `safe_mode=False`), poisoned MCP tools |
| LLM05 Improper output handling | output-direction checks; markdown-image exfiltration and shell-payload signatures |
| LLM06 Excessive agency | tool RBAC per role; irreversible tools blocked; SSRF signature on tool calls |
| LLM07 System prompt leakage | `data_exfiltration` (reveal hidden instructions); output checks |
| LLM10 Unbounded consumption | rate, token and USD budgets per principal/team/global; loop guard; model allowlist |

LLM04 (data poisoning), LLM08 (vector/embedding) and LLM09 (misinformation) are not covered by this layer. They belong in the training and RAG pipelines. RAG results that pass through as tool results do get injection and PII checks.

## Privacy

Monitoring employees' AI use is personal-data processing. In the EU that means GDPR, and possibly works-council agreements. By default the audit log stores the text with every detected span masked (even when the action was only `log`), plus a SHA-256 of the original and the client's IP address (`audit.store_raw_text: false`). Blocked payloads are not stored at all, and a gate-only row (auth, model, budgets) stores no text, since no detector ran on it. A client's own Anthropic login, forwarded in seat mode, is never written anywhere. Content flagged only by a semantic control has no span to mask and is stored as sent. `shadow` and `log` modes allow monitoring without interfering.

## Usage governance

| Piece | Where | Notes |
|---|---|---|
| Attribution | `x-acl-workflow`, `x-acl-task`, `x-acl-session` headers; `ControlLayer.attribute` | A session keeps its declared workflow, so later MCP calls inherit it. Reported usage with a known task joins that task's workflow. |
| Classifier | `controls/workflows.classifier` | One extra `choice` question in the fast tier's existing call, so it adds no extra round trip. Never escalated, never a finding, never a gate. |
| Menu gate | `controls/workflows.check` | Only declared workflows are gated: unknown, disabled, wrong team or role, approval missing, model or tool outside the workflow. |
| Per-run limits | `BudgetLedger._run_check` | A run is one task id. Its tokens and USD are summed from the usage store. |
| Downgrade | `BudgetLedger.downgrade` | Past `budgets.downgrade.at` of any of the caller's daily budgets. Never downgrades to a model the workflow would refuse. |
| Leases | `controls/resources.LeaseTracker` | Start, stop, stop-all and activity tools come from the policy. `handle.pattern` finds the id in the start result, so stop calls close the right lease. The sweep runs every 10 s, and a reclaim calls the first stop tool on the lease's MCP server. |
| Store | `usage.UsageStore` (SQLite, WAL) | One row per LLM call, policy-check call (`resource=guard`, `metered=0`, never charged), closed lease, or reported usage. The in-memory ledger is rebuilt from today's rows at startup. |
| Detections | `detections.RiskEngine` (`layer.detections`) | Rules are code; their parameters live in the policy. One incident per rule per person per window; later evidence joins it. Score = sum of weight x 0.5^(age / half-life) over open and acknowledged incidents. Responses only escalate; recommend-only when `response.auto: false`. |
| Restrictions | `PolicyStore.write_overlay` | Merged over `policy.yaml` (only `principals`, `menu`, `budgets`, `catalog`, `resources`, `detections`, `quarantine`). Validated before it is written. A broken overlay at startup is ignored and reported, and the base policy applies. |

Design choices:

- **Guesses never refuse.** A misclassified prompt would otherwise block legitimate work. Set `menu.require_label: true` to force labels instead.
- **Openness both ways.** `/api/me/*` gives an employee's own tools what is collected, their risk and incidents (`privacy.show_risk_to_employee`), and every admin action or content view about them. This is aimed at GDPR access rights and works-council agreements.
- **Policy checks have a visible cost.** The gateway's own decision-model tokens are reported (`guard`) and never charged to the employee.

Ideas not built yet:

- **Approval for one call.** A sensitive call could be parked until an admin clicks, using the same requests queue that grants use today.
- **Real Cedar.** The evaluator already uses Cedar's model; compiling the catalog and grants to Cedar policies (`cedarpy`) would make them verifiable and shareable.
- **Budget the run, not the day.** Keep a forecast per task, warn when a run is heading past its p90, and suggest stopping before the hard limit.
- **Argent over stdio.** A small `controllayer mcp-wrap -- argent mcp` shim so stdio MCP servers go through the gateway and get leases.
- **Cost-aware routing per workflow.** Light workflows could default to the cheapest allowed model, with the menu price showing what the upgrade costs.
- **Team leaderboard of savings.** Show savings (downgrades, reclaimed zombies, avoided runs), never raw individual usage, so the numbers encourage people rather than single them out.
- **Detection feed.** Ship detection parameters like the signature feed, so thresholds can be tuned centrally.

## Resources, events and Claude Code

| Piece | Where | Notes |
|---|---|---|
| Catalog | `config.ResourceType`, `policy.yaml: catalog` | One entry per resource, named by URN, in one of three classes: consumable, leasable or access grant. Older `resources:` entries without a `type` (lease and price fields) and `budgets.pricing` keys are folded into it at load; `resources:` entries with a `type` are the brokered company resources. The legacy views the lease tracker (`Policy.legacy_resources`) and pricing read are derived from the catalog and excluded from dumps, so a dump-and-validate round trip is stable. |
| Evaluator | `controls/resources.authorize` | Takes Cedar's (principal, action, resource, context) and returns a `Decision`. Consumables are decided by budgets, leasables by concurrency caps and the workflow, and access grants by a live grant or by `approval: workflow` plus a workflow that includes the resource. It is used on every gateway and hook tool call, and by `POST /v1/authorize`. |
| Grants | `principals.<id>.grants` in the admin overlay | Shaped like RFC 9396 `authorization_details`. They are time-boxed, clamped to the resource's `max_minutes`, and optionally scoped to one workflow. A live grant lets its tools past the role list, the workflow's tool list and the irreversible list, but never past quarantine. |
| Events | `usage.events`, `events.Ingestor` | One activity stream. Verdicts are mirrored by the engine; leases, incidents and reports write their own events; external producers come in through `ingest()`. An event that carries cost and is metered also writes a usage row. The price comes from the catalog when the producer sent none. |
| Claude Code | `claude_code.py`, `gateway/otel.py`, `gateway/hooks.py` | Telemetry is observed and hooks enforce. Money comes from `api_request` log events only, not the duplicate cost metrics. Hooks run deterministic tiers only, because they sit on the user's path. Permission mode and MCP servers come from hook input: the telemetry's MCP connection event carries no server name. |
| Exports | `focus.py`, `/admin/export/backstage` | Spend goes out as FOCUS 1.1 with attribution in `x_` columns and Tags. Importing that file into an empty data dir gives the same totals per resource and person (tested). |
| Seeder | `seed/` | A separate package that the server never imports (tested). It writes through `Ingestor` with historical timestamps, and is deterministic for a given seed and end time. |

## Known gaps

- Approvals cover workflows, quota and time-boxed access grants. One call cannot yet be parked while it waits for a human click: the agent retries after the grant is approved.
- The company services are in-process mocks. A production backend would call each real API with the injected credential.
- Source masking covers the SQL services. Records from the other services rely on the content checks alone. Writes into company systems (S3, including its public bucket, Notion, GitHub issues) are not treated as egress.
- Insider-risk scores live in memory and reset on restart; manual levels and grants persist.
- The Privacy Filter sidecar and real decision models have not been run on the build VM; their contracts are tested against mocks.
- Claude Code telemetry needs its `OTEL_*` variables in the process environment (shell, user or managed settings). In a test with 2.1.288, project-level `env` enabled the hooks but not telemetry. HTTP hooks fail open; the command-hook variant fails closed.
- Budgets, usage, leases and incidents persist in a local SQLite file. Rate-limit windows, the loop guard and the detection baselines for new clients and tool drift are in memory. Multiple replicas would need a shared store (Postgres or Redis).
- Leases only see resources started through MCP tools or reported to `/v1/usage`. A simulator started by hand is invisible.
- Chat streaming is emulated: the upstream is called without streaming and the checked reply is sent as one chunk. `/v1/messages` relays a real stream, but each content block arrives whole once checked, so long text appears a block at a time.
- `/v1/messages` has been exercised with Claude Code 2.1.280 against the mock and a local fake of the API (seat pass-through included), not against `api.anthropic.com`. Redacting history rewrites signed content: the upstream then rejects the thinking signature and Claude Code retries without earlier thinking.
- The MCP proxy speaks JSON-RPC over plain HTTP POST. SSE sessions and stdio servers are not proxied.
- The heuristic backend is a keyword stand-in for demos without models, not a classifier. Coding agents read files and docs that mention its keywords, so with it a real session meets false positives that a decision model would judge in context.
