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

## Identities, resources and grants

API keys map to principals of kind `human` or `agent`, and every agent has an owner. The resource catalog (`resources:` in the policy) is security-owned and lists who is entitled to each resource. Grants are runtime state in `data/state.json`, written atomically and changed only through the panels. Security edits the policy; employees edit grants.

Each catalog entry of `type: service` names one service in `controllayer/services.py` (`connection.service`) and the environment variable holding its credential (`connection.secret_env`). A service is catalogued at most once, so a tool name identifies the resource it uses. Each tool declares the scope it needs, and the catalog's `scopes` must be a subset of the scopes the service's tools use: a scope left out keeps its tools from every grant. The policy loader rejects an unknown service, a scope no tool uses and a service catalogued twice.

A broker call has a resource and a scope, both given by the tool. It passes the gate only if all of these hold at that moment:
- the agent holds a grant with that scope;
- the grant has not expired;
- the owner is still entitled;
- the resource is not suspended.

Such decisions are never cached, because expiry depends on time. `tools/list` applies the same rules, so it shows only the tools the caller can use now. The tool's arguments are validated against its schema (unknown keys, types, enums, ranges) before the backend runs. SQL tools accept one `SELECT` or `WITH ... SELECT` statement with no write, DDL or session keyword outside literals, quoted names and comments, and run it on a read-only database with a statement timeout and a cap on value and result size. A grant's expiry is set once, when it is created or when an expired grant is renewed. Changing its scopes (`PATCH /me/api/grants/{agent}/{resource}`) keeps the expiry and works only on an active grant. A new grant over a live one is refused. Reaching a catalogued MCP server needs a grant too, but per-tool RBAC and the irreversible-tool rule still apply on that server. Agents cannot reach uncatalogued servers at all.

## Insider risk

`RiskEngine` keeps a decaying score per principal. Points come from each finding's *proposed* action, so shadowed and capped findings still count as intent, plus category weights. Rate limits, outages and auth failures are not evidence. A person's own level is the one security set manually if any (an override, in either direction). Otherwise it is the higher of the score-based level and the strongest unexpired external signal. The effective level is the higher of that and, for agents, the owner's own level. External signals (`POST /admin/risk/{pid}/signal`) are stored per source in `data/state.json` with their expiry. Expired ones are ignored on read and dropped on the next write. The endpoint is the one `/admin` path that skips the admin token; it accepts only a token from `identity.integrations`, never one equal to the admin token. A rise of the auto level while an override holds the person raises an alert, so security sees the drift. The level selects the policy variant (`watch_controls`), turns on raw capture, or blocks (`restricted`). It is part of the verdict cache key.

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

The Privacy Filter sidecar returns BIOES-decoded spans. The gateway applies `min_score`, maps labels to actions and merges the spans with regex findings. In chat, reversible labels become placeholders numbered per request, and are put back after the reply passes its own checks. PII values the caller supplied in the same request are not re-redacted in the reply. Overrides (user header or decision model) downgrade PII findings to `log`, never past `override_max`, and are written to the audit trail.

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

Monitoring employees' AI use is personal-data processing. In the EU that means GDPR, and possibly works-council agreements. By default the audit log stores the text with every detected span masked (even when the action was only `log`), plus a SHA-256 of the original and the client's IP address (`audit.store_raw_text: false`). Blocked payloads are not stored at all. Content flagged only by a semantic control has no span to mask and is stored as sent. `shadow` and `log` modes allow monitoring without interfering.

## Known gaps

- No approval workflow yet: irreversible tools are simply blocked.
- The company services are in-process mocks. A production backend would call each real API with the injected credential.
- Risk scores live in memory and reset on restart; manual levels and grants persist.
- The Privacy Filter sidecar and real decision models have not been run on the build VM; their contracts are tested against mocks.
- Budgets and metrics are in-memory, so a restart resets them. Multiple replicas would need Redis for shared counters.
- Streaming responses are buffered and released as a single checked chunk.
- The MCP proxy speaks JSON-RPC over plain HTTP POST. SSE sessions and stdio servers are not proxied.
- The heuristic backend is a keyword stand-in for demos without models, not a classifier.
