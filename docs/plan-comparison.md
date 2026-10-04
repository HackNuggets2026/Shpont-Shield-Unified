# Your plan vs. what's built

## Architecture in one picture

```
 Employee apps / chat UIs ──OpenAI API──┐
 Agents ──────────────────MCP (JSON-RPC)─┼──► GATEWAY ──► LLM (Ollama / OpenAI / ...)
 Custom code ─────────────/v1/guard SDK──┘        └──────► real MCP servers (Jira, DB, files...)
```

The gateway **is** the proxy. "MCP" means it also proxies agent → tool traffic, because in agentic setups most leaks and attacks travel through tools: poisoned tool descriptions, documents with hidden instructions, destructive calls. The "demo MCP server" is just a booby-trapped fake target.

Every payload goes through one pipeline:

1. **Gates:** identity, model allowlist, tool RBAC
2. **Budgets:** tokens, money, rate limits, loop guard
3. **Deterministic:** secrets, PII regexes with checksums, attack-signature feed
4. **tev1:0.8b:** answers the policy questions
5. **nimble:** re-answers only the uncertain ones
6. **Decision:** `allow < log < warn < redact < block`

Everything is configured in one hot-reloaded `policy.yaml`. An ethics rule is one YAML entry.

## Alignment

| Your idea | Status |
|---|---|
| Corporate guardrails: ethics, client privacy, project confidentiality | **Shared.** Semantic controls in YAML, per-team overrides |
| Packaged as a proxy | **Shared,** plus the MCP proxy for agents |
| Jev / tev1 / nimble as decision model | **Shared, the core.** Two-tier cascade on `/v1/systemone`. Not yet run on real models (this VM can't host them) |
| Flag malicious employees, monitor closely, silently alert security | **Built:** decaying risk score, watch (stricter policy + full capture) / restricted levels, silent alerts to console, file or webhook |
| OpenAI Privacy Filter for PII, minimally invasive, overridable | **Built:** sidecar + contextual spans, reversible masking restored in replies, user and model overrides that never lift a block |
| Employee panel | **Built:** `/me`. Usage, activity, and grants of company resources (servers, credentials, SaaS, MCP) to their agents |
| Security panel | **Built:** `/security`. Everything above plus risk levels, alerts, grant revocation, resource suspension |

## Why Privacy Filter fits so well

Decision models say *whether* text contains client data, not *where*, so semantic redaction has to withhold the whole message. Privacy Filter (1.5B parameters, 50M active, Apache 2.0) tags the spans. It complements the regexes, which stay better for checksum-validated cards, IBANs and keys.

- **Minimally invasive:** reversible masking. The model sees `<PRIVATE_PERSON_1>`; the gateway puts the real name back into the reply.
- **User override:** an `x-pii-override: <reason>` header, honoured only for permitted roles and always audited.
- **Model override:** the fast decision model (tev1) is asked "is this PII necessary for the task?" If it is (e.g. support replying to a client), the PII is allowed and logged.

## Only in the build, and required by the brief

These are budget and cost governance, historical-attack signatures, reporting and audit export, and the automated test suite. Robustness, reporting and the test suite are 65% of the score combined.

## Monitoring note

Under GDPR and Polish Labour Code art. 22³, monitoring employees must be disclosed in advance. "Silent" should mean the individual alert isn't shown to the employee, not that the monitoring itself is secret.
