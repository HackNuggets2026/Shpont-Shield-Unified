# Employee monitoring and insider-risk tools: integration research

Checked 2026-10-03. Licenses read from each repo's LICENSE file, activity from GitHub releases.

**What we already emit:** silent alerts (`file` JSONL or `webhook` POST, each as native JSON, OCSF 1.9 Detection Finding or ECS), audit log (`/admin/audit/export` as JSONL/CSV, or OCSF/ECS NDJSON with admin actions and alerts included; `/admin/events`), Prometheus `/metrics`.
**What we already accept:** `POST /admin/risk/{pid}/signal` `{"level"` or `"score", "ttl_seconds", "source", "reason"}`, authenticated by a per-integration token from `identity.integrations`. This is the hook for risk signals coming back from another tool. A signal only raises a person's level, is capped at the integration's `max_level` and expires. `POST /admin/risk/{pid}` is security's manual override and is not for integrations.

## Comparison

| Tool | License | Last release | What it is | Ingest surface | Risk signal back to us | Our effort |
|---|---|---|---|---|---|---|
| **Wazuh** | GPL-2.0 (indexer Apache-2.0) | 4.14.8, 2026-09 (5.0 in RC, ECS-native) | Free XDR/SIEM, huge SMB and public-sector base | JSON log file or syslog read by agent, custom decoder + rules | Active response or `integratord` script runs on rule match, can POST to our API | S: file sink exists; add decoder/rules XML (~0.5 day) |
| **Elastic Security** | AGPL-3.0 / SSPL / ELv2; entity risk scoring needs Platinum | 9.5.4, 2026-09 | SIEM with entity analytics: 0-100 user risk score, watchlists (9.4) | `_bulk` NDJSON in ECS, or Filebeat/Elastic Agent on our JSONL | Read `risk-score.risk-score-latest-*` for `user.name`; watchlist API | M: ECS mapping + bulk sink + poller (~1 day) |
| **OpenSearch Security Analytics** | Apache-2.0 | OpenSearch 3.9.0, 2026-09 | Sigma-rule detectors, custom log types, native OCSF | `_bulk` JSON, OCSF or custom log type | Detector findings -> alerting webhook to our API | M: OCSF sink + one Sigma rule (~1 day) |
| Graylog Open | SSPL (not OSI) | rolling, active 2026-10 | Log mgmt; anomaly detection only in paid Illuminate | GELF HTTP/UDP, syslog, raw JSON | Event notifications (HTTP) | S, but low buyer value |
| Security Onion | ELv2 | 3.3.0, 2026-09 | NSM/SOC distro on Elastic | Elastic Agent / syslog | Via Elastic | Same as Elastic, no extra reach |
| osquery / Fleet | Apache-2.0 or GPL-2.0 / MIT core + EE | 5.23.1 2026-06 / 4.92.2 2026-09 | Endpoint SQL telemetry | Not a sink for us | We could trigger a Fleet live query on a watched user's host | M, endpoint not our domain |
| Velociraptor | AGPL-3.0 | 0.77.2, 2026-08 | DFIR hunts on endpoints | gRPC API, server artifacts | Same idea: our `restricted` level kicks off a collection | M |
| TheHive 5 + Cortex | TheHive 5 proprietary (free community license, registration); Cortex AGPL-3.0 4.1.0 2026-06 | TheHive 4 archived | Case management | `POST /api/v1/alert` with observables | Analyst closes case -> our `normal` | S (~0.5 day), but SOC-only buyers |
| DFIR-IRIS | LGPL-3.0 | 2.4.29, 2026-08 | Open case management | REST alerts API | Webhooks module | S |
| Shuffle / Tracecat | AGPL-3.0 | 2.3.0 / 1.0.1, 2026-09 | Open SOAR | Webhook trigger | Any workflow can call our API | XS: our webhook sink already fits |
| MISP / OpenCTI | AGPL-3.0 / Apache-2.0 CE | 2.5.48 / 7.x, 2026-09/10 | Threat intel sharing | STIX/MISP events | IOC feeds could feed our signature feed | Not insider risk; skip |
| Apache Metron / Spot | Apache-2.0 | archived (2025 / 2023) | Former open UEBA | - | - | Dead, skip |
| Langfuse | MIT core + EE dirs (copyright ClickHouse, Inc.) | 4.50.0, 2026-10 | LLM observability | OTLP `/api/public/otel/v1/traces`, `POST /scores` | None (not a risk tool) | S: per-user AI usage traces; sells to platform teams, not security |
| OpenLIT | Apache-2.0 | 2.1.0, 2026-09 | OTel-native LLM observability | OTLP gen_ai semconv | None | S |
| Arize Phoenix | ELv2 | 20.19.0, 2026-10 | LLM tracing/evals | OTLP | None | S |
| Helicone | Apache-2.0 | last tagged 2025-08, commits 2026-09 | LLM proxy + observability | Is itself a proxy; overlaps us | None | Competitor-adjacent, skip |

**Commercial, for positioning (none open, all endpoint/SaaS-telemetry first):**

| Product | AI angle | Integration surface |
|---|---|---|
| Microsoft Purview Insider Risk | "Risky AI usage" template, Copilot-centric | Insider Risk Indicators connector (preview) imports third-party detections as custom indicators; HR connector is CSV |
| Mimecast Incydr (ex-Code42) | Uploads/paste into ~14 AI sites as indicators | Exfil-centric, user risk profiles |
| DTEX InTERCEPT, Proofpoint ITM | Endpoint behavior, "AI" as one more destination | SIEM forwarders |
| Teramind, ActivTrak | Screen/keystroke/OCR capture of ChatGPT prompts; productivity framing | Exports, BI |
| Harmonic, Prompt Security (SentinelOne, 2025), Nightfall | Browser/endpoint AI-DLP | Proprietary |

**Positioning:** they all watch the endpoint or browser and guess what went to the AI. We sit on the actual LLM/MCP/agent traffic, with authorship and agent-to-owner attribution, and can enforce (watch policy, restrict) instead of just reporting. That makes us a high-fidelity *signal source* for their risk scores, not a competitor to them.

## Top 3 integrations (startup potential x hackathon effort)

1. **Wazuh (alerts in, watch level back).** Our file sink plus a Wazuh decoder and rules turns gateway alerts into Wazuh alerts, and an active-response script calls `POST /admin/risk/{pid}/signal` when Wazuh sees the same user doing something risky on the endpoint (built: `integrations/wazuh`). Biggest free install base (17k stars, SMB/EU/public sector without Purview E5 budget), about half a day, demoable end to end.
2. **Elastic Security entity risk (enrich their user score, read it back).** Ship ECS events with `user.name`, so Elastic detection rules fire and feed the 0-100 entity risk score, then poll `risk-score-latest` and raise our level when the score crosses a threshold. Elastic Platinum customers already pay for entity analytics and lack AI-usage telemetry. About 1 day.
3. **OCSF export to OpenSearch Security Analytics (one format, many SIEMs).** Emit OCSF Detection Findings, load them as a custom log type with one Sigma rule, and the same export also feeds Amazon Security Lake, Splunk and other OCSF consumers. This is the "plugs into whatever SIEM you have" sales line. About 1 day, and it reuses the schema work from pick 2.

Honorable mention: the Purview Insider Risk Indicators connector is the strongest enterprise sales story ("AI gateway detections inside Purview IRM"), but it needs an E5 tenant, so it is not hackathon-feasible.

**Level precedence integrators can rely on:**

1. A level security set by hand is the person's level, in either direction. Signals and scores do not change it, but a rise of the auto level underneath still raises a silent alert.
2. Without one (auto), the level is the higher of the score-based level and the strongest active signal.
3. An agent is at least as restricted as its owner.

Each integration source keeps one signal, which its next signal replaces. Sending `normal` withdraws it. A source can never touch another integration's signals or security's override.

## Schema recommendation

**Canonical: OCSF 1.9.** Alerts become `Detection Finding` (class 2004). Audit events become `API Activity` (6003) / `Application Activity`, with the `ai_operation` profile (added in 1.8; `ai_model`, `message_context` token counts; 1.9 adds `delegation`, which maps directly to our agent-on-behalf-of-owner). Put the person in `actor.user.uid`, the agent in `actor.app_name` / `delegation`, and score and level in `risk_score` / `risk_level`. OCSF is native in OpenSearch Security Analytics, Amazon Security Lake and Splunk, and it is the only schema with first-class AI-agent fields today.

**Plus a thin ECS view** (`user.name`, `event.kind/category/action`, `rule.name`, `event.risk_score`) for Elastic and Wazuh 5.0, which are ECS-native. It is one mapping function over the same event, not a second pipeline.

## Sources

- Wazuh: [repo](https://github.com/wazuh/wazuh), [integratord / external APIs](https://documentation.wazuh.com/current/user-manual/manager/integration-with-external-apis.html), [custom active response](https://documentation.wazuh.com/current/user-manual/capabilities/active-response/custom-active-response-scripts.html), [5.0 engine and ECS](https://blog.pytoshka.me/en/post/wazuh-4-14-7-vs-5-0-beta-5/)
- Elastic: [entity risk scoring](https://www.elastic.co/docs/solutions/security/advanced-entity-analytics/entity-risk-scoring), [requirements (Platinum)](https://www.elastic.co/docs/solutions/security/advanced-entity-analytics/entity-risk-scoring-requirements), [watchlists](https://www.elastic.co/docs/solutions/security/advanced-entity-analytics/watchlists), [watchlists blog](https://www.elastic.co/security-labs/entity-analytics-watchlists), [ECS](https://github.com/elastic/ecs)
- OpenSearch: [Security Analytics](https://docs.opensearch.org/latest/security-analytics/), [custom rules](https://opensearch.org/blog/how-to-create-custom-threat-detection-rules/), [OCSF support](https://aws.amazon.com/about-aws/whats-new/2023/10/security-analytics-opensearch-service-ocsf-custom-logs/)
- OCSF: [schema repo](https://github.com/ocsf/ocsf-schema), [changelog (ai_operation 1.8, delegation 1.9)](https://github.com/ocsf/ocsf-schema/blob/main/CHANGELOG.md), [draft AI Agent Activity class](https://github.com/ocsf/ocsf-schema/pull/1754)
- Graylog: [repo (SSPL)](https://github.com/Graylog2/graylog2-server), [anomaly detection (Illuminate)](https://go2docs.graylog.org/current/what_more_can_graylog_do_for_me/anomaly_detection.html)
- [Security Onion](https://github.com/Security-Onion-Solutions/securityonion), [osquery](https://github.com/osquery/osquery), [Fleet](https://github.com/fleetdm/fleet), [Velociraptor](https://github.com/Velocidex/velociraptor)
- TheHive: [licenses](https://docs.strangebee.com/thehive/installation/licenses/about-licenses/), [alerts](https://docs.strangebee.com/thehive/user-guides/analyst-corner/alerts/about-alerts/), [Cortex](https://github.com/TheHive-Project/Cortex); [DFIR-IRIS](https://github.com/DFIR-IRIS/iris-web), [Shuffle](https://github.com/Shuffle/Shuffle), [Tracecat](https://github.com/TracecatHQ/tracecat)
- [MISP](https://github.com/MISP/MISP), [OpenCTI](https://github.com/OpenCTI-Platform/opencti), [Metron (archived)](https://github.com/apache/metron), [Spot (archived)](https://github.com/apache/incubator-spot)
- LLM observability: [Langfuse OTel](https://langfuse.com/integrations/native/opentelemetry), [Langfuse API](https://langfuse.com/docs/api-and-data-platform/features/public-api), [OpenLIT](https://github.com/openlit/openlit), [Phoenix](https://github.com/Arize-ai/phoenix), [Helicone](https://github.com/Helicone/helicone)
- Purview: [import third-party insider risk indicators](https://learn.microsoft.com/en-us/purview/import-insider-risk-indicators), [policy templates (Risky AI usage)](https://learn.microsoft.com/en-us/purview/insider-risk-management-policy-templates), [HR connector](https://learn.microsoft.com/en-us/purview/import-hr-data)
- Commercial: [Incydr GenAI tools](https://mimecastsupport.zendesk.com/hc/en-us/articles/41280847076371-Incydr-Generative-AI-Tools), [ITM comparison 2026](https://guptadeepak.com/tools/top-5-insider-threat-management-tools-2026/), [Teramind AI governance](https://siliconangle.com/2026/03/03/teramind-launches-agentic-ai-visibility-policy-platform-ai-tools/), [Prompt Security](https://en.wikipedia.org/wiki/Prompt_Security), [Harmonic](https://www.harmonic.security/solutions/dlp-for-genai)
