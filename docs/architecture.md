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

Structured payloads (tool arguments, tool results, non-text message fields) are flattened to raw text for inspection, with JSON-in-strings decoded. Redaction is applied to every key and string in place and then re-checked. If anything is still detected (a card number stored as an integer, a secret split across fields), the payload is refused, never forwarded.

The directions are `input`, `output`, `tool_call`, `tool_result` and `tool_description`. Each control declares which directions it inspects. The chat proxy checks every message in the request on the way in, because the client owns the history and can forge it; repeats are served from a verdict cache. It checks the completion on the way out. The MCP proxy checks arguments, every result and the tool list itself, including names and schema strings, so a poisoned tool is removed before the agent ever sees it.

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

Commercial models are priced per 1M input/output tokens. Local models are priced per compute-second, using measured upstream latency times `usd_per_compute_second`. Limits apply per principal, per team and globally per UTC day. The loop guard stops an agent that repeats the same call more than N times in a window.

## OWASP mapping

| OWASP LLM Top 10 (2025) | Control |
|---|---|
| LLM01 Prompt injection | `prompt_injection` semantic control on input, tool_result and tool_description; jailbreak signatures; tool-poisoning signature |
| LLM02 Sensitive information disclosure | secrets + PII detectors in both directions; `confidential_output`; `data_exfiltration` |
| LLM03 Supply chain | signature feed: pickle opcodes, `weights_only=False`, `trust_remote_code`, poisoned MCP tools |
| LLM05 Improper output handling | output-direction checks; markdown-image exfiltration and shell-payload signatures |
| LLM06 Excessive agency | tool RBAC per role; irreversible tools blocked; SSRF signature on tool calls |
| LLM07 System prompt leakage | `data_exfiltration` (reveal hidden instructions); output checks |
| LLM10 Unbounded consumption | rate, token and USD budgets per principal/team/global; loop guard; model allowlist |

LLM04 (data poisoning), LLM08 (vector/embedding) and LLM09 (misinformation) are not covered by this layer. They belong in the training and RAG pipelines. RAG results that pass through as tool results do get injection and PII checks.

## Privacy

Monitoring employees' AI use is personal-data processing. In the EU that means GDPR, and possibly works-council agreements. By default the audit log stores the text with every detected span masked (even when the action was only `log`), plus a SHA-256 of the original (`audit.store_raw_text: false`). Blocked payloads are not stored at all. Content flagged only by a semantic control has no span to mask and is stored as sent. `shadow` and `log` modes allow monitoring without interfering.

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
| Detections | `detections.RiskEngine` | Rules are code; their parameters live in the policy. One incident per rule per person per window; later evidence joins it. Score = sum of weight x 0.5^(age / half-life) over open and acknowledged incidents. Responses only escalate; recommend-only when `response.auto: false`. |
| Restrictions | `PolicyStore.write_overlay` | Merged over `policy.yaml` (only `principals`, `menu`, `budgets`, `catalog`, `resources`, `detections`, `quarantine`). Validated before it is written. A broken overlay at startup is ignored and reported, and the base policy applies. |

Design choices:

- **Guesses never refuse.** A misclassified prompt would otherwise block legitimate work. Set `menu.require_label: true` to force labels instead.
- **Openness both ways.** The employee page shows what is collected, their risk and incidents (`privacy.show_risk_to_employee`), and every admin action or content view about them. This is aimed at GDPR access rights and works-council agreements.
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
| Catalog | `config.ResourceType`, `policy.yaml: catalog` | One entry per resource, named by URN, in one of three classes: consumable, leasable or access grant. The older `resources:` and `budgets.pricing` keys are folded into it at load. The legacy views the lease tracker and pricing read are derived from the catalog and excluded from dumps, so a dump-and-validate round trip is stable. |
| Evaluator | `controls/resources.authorize` | Takes Cedar's (principal, action, resource, context) and returns a `Decision`. Consumables are decided by budgets, leasables by concurrency caps and the workflow, and access grants by a live grant or by `approval: workflow` plus a workflow that includes the resource. It is used on every gateway and hook tool call, and by `POST /v1/authorize`. |
| Grants | `principals.<id>.grants` in the admin overlay | Shaped like RFC 9396 `authorization_details`. They are time-boxed, clamped to the resource's `max_minutes`, and optionally scoped to one workflow. A live grant lets its tools past the role list, the workflow's tool list and the irreversible list, but never past quarantine. |
| Events | `usage.events`, `events.Ingestor` | One activity stream. Verdicts are mirrored by the engine; leases, incidents and reports write their own events; external producers come in through `ingest()`. An event that carries cost and is metered also writes a usage row. The price comes from the catalog when the producer sent none. |
| Claude Code | `claude_code.py`, `gateway/otel.py`, `gateway/hooks.py` | Telemetry is observed and hooks enforce. Money comes from `api_request` log events only, not the duplicate cost metrics. Hooks run deterministic tiers only, because they sit on the user's path. Permission mode and MCP servers come from hook input: the telemetry's MCP connection event carries no server name. |
| Exports | `focus.py`, `/admin/export/backstage` | Spend goes out as FOCUS 1.1 with attribution in `x_` columns and Tags. Importing that file into an empty data dir gives the same totals per resource and person (tested). |
| Seeder | `seed/` | A separate package that the server never imports (tested). It writes through `Ingestor` with historical timestamps, and is deterministic for a given seed and end time. |

## Known gaps

- Approvals cover workflows, quota and time-boxed access grants. One call cannot yet be parked while it waits for a human click: the agent retries after the grant is approved.
- Claude Code telemetry needs its `OTEL_*` variables in the process environment (shell, user or managed settings). In a test with 2.1.288, project-level `env` enabled the hooks but not telemetry. HTTP hooks fail open; the command-hook variant fails closed.
- Budgets, usage, leases and incidents persist in a local SQLite file. Rate-limit windows, the loop guard and the detection baselines for new clients and tool drift are in memory. Multiple replicas would need a shared store (Postgres or Redis).
- Leases only see resources started through MCP tools or reported to `/v1/usage`. A simulator started by hand is invisible.
- Streaming responses are buffered and released as a single checked chunk.
- The MCP proxy speaks JSON-RPC over plain HTTP POST. SSE sessions and stdio servers are not proxied.
- The heuristic backend is a keyword stand-in for demos without models, not a classifier.
