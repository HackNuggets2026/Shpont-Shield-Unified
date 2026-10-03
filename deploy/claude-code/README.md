# Claude Code → Shpont Shield

Copy one of these into a project as `.claude/settings.json` (or `.claude/settings.local.json`):

| File | Enforcement | If the gateway is down |
|---|---|---|
| `settings.json` | HTTP hooks | Claude Code carries on (HTTP hooks fail open) |
| `settings.fail-closed.json` | command hook `shield-hook.sh` (copy it to `.claude/`) | prompts and tool calls are blocked (exit 2) |

Both also send telemetry (OTLP over HTTP JSON) to the gateway every 5 s: cost and tokens per API call, tool decisions and results, lines of code, commits and PRs.

**Telemetry needs its variables in the process environment.** In a test with Claude Code 2.1.288, the hooks picked up `SHIELD_*` from the project settings `env`, but telemetry stayed off. Export the `OTEL_*` and `CLAUDE_CODE_ENABLE_TELEMETRY` variables in the shell, or put them in user or managed settings:

```bash
source deploy/claude-code/telemetry.env && claude
```

Change `dev-alice-key` to the employee's key (`SHIELD_KEY` and `OTEL_EXPORTER_OTLP_HEADERS`), and `SHIELD_WORKFLOW` / `SHIELD_TASK` (and `workflow=…,task=…` in `OTEL_RESOURCE_ATTRIBUTES`) to the work being done. If header interpolation does not pick up `SHIELD_*` from the settings `env`, export them in the shell before starting `claude`.

What the hooks enforce, with the same `policy.yaml` as the gateway:
- **Prompts** (UserPromptSubmit): secrets, PII and attack signatures; budgets (Claude Code spend from telemetry counts against them); quarantine.
- **Tool calls** (PreToolUse): the role's and the workflow's tool lists (`pr_review` may not `Edit`), access grants (`mcp__*__query_prod_db` needs a live grant), simulator and VM caps, secrets in tool input.
- **Reported to detections**: bypass permission mode, MCP servers not on the approved list, a run of rejected tool calls.

In production, roll the same settings out as managed settings so users cannot remove them.
