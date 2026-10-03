# Claude Code through the control layer

Claude Code talks to the gateway's Anthropic Messages endpoint (`/v1/messages`). Every prompt, file it reads, command it runs and edit it makes passes the same checks as chat, and the gateway answers with Claude Code's own streaming format. Two ways to sign in:

| Mode | Employee holds | Upstream is called with | Billing | Use when |
|---|---|---|---|---|
| **Gateway key** | a control-layer key, fetched by `apiKeyHelper` | the org's Console key (`ANTHROPIC_API_KEY` on the gateway) | per token, to the org | the company buys API usage |
| **Seat** | their Team/Enterprise claude.ai login, plus a control-layer key in `x-acl-key` | the employee's own login, forwarded untouched | the seat | the company buys seats |

In seat mode the gateway forwards `Authorization` and `anthropic-beta` (which carries the OAuth capability) and never logs or stores them. The control-layer key in `x-acl-key` is what names the employee; without a known one the request is refused.

## Files

| File | What |
|---|---|
| [`managed-settings.gateway-key.json`](managed-settings.gateway-key.json) | Gateway-key mode: base URL, `apiKeyHelper`, gateway as the only provider, managed hooks and MCP servers only |
| [`managed-settings.seat.json`](managed-settings.seat.json) | Seat mode: base URL, claude.ai login pinned to the company org (`forceLoginMethod`, `forceLoginOrgUUID`), same locks |
| [`50-acl-key.json.example`](50-acl-key.json.example) | Seat mode: the per-device drop-in carrying the employee's `x-acl-key` |
| [`acl-gateway-key`](acl-gateway-key) | Gateway-key mode: an `apiKeyHelper` that prints the employee's key |

## Gateway side

1. In `policy.yaml`, give each employee a key under `identity.api_keys`, keep `claude-*` in `models.allowed` and set the Anthropic upstream:
   ```yaml
   upstream:
     anthropic:
       backend: anthropic                 # `mock` answers with canned replies
       url: https://api.anthropic.com
       api_key_env: ANTHROPIC_API_KEY     # gateway-key mode only
       passthrough_auth: true             # seat mode; false makes every call use the org key
   ```
2. Gateway-key mode: start the gateway with the org's Console key in `ANTHROPIC_API_KEY`.
3. Serve it over HTTPS at the exact URL you distribute, with no redirect. Exempt `/v1/messages` from WAF request-body rules: Claude Code prompts carry source code and XML-like tags that trip XSS rules.
4. Budgets: Claude Code resends the whole conversation every turn, and cached prompt tokens count toward `tokens_per_day`. Size token limits for that, or cap with `usd_per_day`, which prices cache reads at 0.1x.

## Rollout

1. **Pick the file** for your mode, replace `https://ai-gateway.example.com` (and, in seat mode, the org UUID from claude.ai admin settings) and deploy it as `managed-settings.json`:
   - macOS: `/Library/Application Support/ClaudeCode/managed-settings.json`
   - Linux and WSL: `/etc/claude-code/managed-settings.json`
   - Windows: `C:\Program Files\ClaudeCode\managed-settings.json`

   A managed `ANTHROPIC_BASE_URL` cannot be overridden by the developer.
2. **Give each employee their key.**
   - Gateway-key mode: install `acl-gateway-key` as `/usr/local/bin/acl-gateway-key` and make it read your secrets store. Claude Code sends its output in both `Authorization` and `x-api-key`; the gateway accepts either.
   - Seat mode: deploy `50-acl-key.json.example`, filled in per device, as `managed-settings.d/50-acl-key.json` next to `managed-settings.json` (MDM templates do this). Do not use `apiKeyHelper`, `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` here: they replace the seat login, and Claude Code refuses to start when they are combined with `forceLoginOrgUUID`.
3. **Check one machine.** `claude` starts without a login screen (gateway-key mode) or on the company login (seat mode). `/status` shows `Anthropic base URL` with the gateway and managed settings among the setting sources. Send a prompt: the security panel (`/security`) shows the request under the employee's name.
4. **Try a block.** Ask Claude Code to `cat` a file holding `AKIAIOSFODNN7EXAMPLE`. Claude Code shows `API Error: 400 blocked by policy: secrets/aws_access_key: 1 x aws_access_key`, and the panel shows the block.

Quick test without managed settings, from a shell:

```bash
# Gateway-key mode
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 ANTHROPIC_AUTH_TOKEN=dev-alice-key claude -p "hello"
# Seat mode (signed in to claude.ai)
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 ANTHROPIC_CUSTOM_HEADERS="x-acl-key: dev-alice-key" claude
```

With the shipped policy (`upstream.anthropic.backend: mock`) the first command needs no Anthropic key and answers `(mock claude-…) You asked about: hello`.

## Notes

- `allowedProviders: ["customEndpoint"]` needs Claude Code 2.1.285 or later. Pin the version with `requiredMaximumVersion` once tested.
- A blocked tool result stays in Claude Code's history, so every later request of that conversation is blocked too: `/rewind` past it or `/clear`.
- `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` stops telemetry and update checks outside the gateway path; it also turns off auto-updates. The fast-mode check and the WebFetch domain check still go to `api.anthropic.com` directly.
- Personal Pro/Max logins: seat mode pins the company org with `forceLoginOrgUUID`; gateway-key mode does not need a login at all.
- What the gateway does not see: the desktop app (it has its own third-party inference configuration), cloud sessions, claude.ai connectors and Remote Control.
- When PII is masked in history, the upstream sees placeholders in content that the model signed earlier. The upstream may then reject old thinking signatures; Claude Code retries without earlier thinking blocks.
