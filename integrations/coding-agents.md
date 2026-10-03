# Other coding agents

These agents speak the OpenAI chat-completions API, which the gateway serves at `http://gateway:8787/v1` (checks as in the main README). Each employee uses their own control-layer key as the API key. Model names must be in `models.allowed` and served by the chat upstream (`upstream.backend`: Ollama, an OpenAI-compatible server, or `mock` with `mock-*` names). For [Claude Code](claude-code/README.md), and for agents set to their Anthropic provider, use the Messages endpoint at the same base URL.

MCP servers go through `http://gateway:8787/mcp/<server>` with the same key as a bearer token; company services are at `/mcp/company`.

## OpenCode

`opencode.json` (per project, or `~/.config/opencode/opencode.json`), with the key in `ACL_KEY`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "acl": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "AI Control Layer",
      "options": { "baseURL": "https://ai-gateway.example.com/v1", "apiKey": "{env:ACL_KEY}" },
      "models": { "qwen3:8b": { "name": "Qwen3 8B" } }
    }
  },
  "mcp": {
    "company": {
      "type": "remote",
      "url": "https://ai-gateway.example.com/mcp/company",
      "headers": { "Authorization": "Bearer {env:ACL_KEY}" }
    }
  }
}
```

To use Claude models through `/v1/messages` instead, point the built-in provider at the gateway: `"provider": {"anthropic": {"options": {"baseURL": "https://ai-gateway.example.com/v1", "apiKey": "{env:ACL_KEY}"}}}`. To enforce either for everyone, ship it as OpenCode's managed configuration with `enabled_providers` limited to it (see OpenCode's enterprise docs).

## Continue

`config.yaml`:

```yaml
name: Company
version: 0.0.1
schema: v1
models:
  - name: Qwen3 via control layer
    provider: openai
    model: qwen3:8b
    apiBase: https://ai-gateway.example.com/v1
    apiKey: ${{ secrets.ACL_KEY }}
```

## Cline and Roo Code

Settings → API Provider **OpenAI Compatible**: Base URL `https://ai-gateway.example.com/v1`, API Key = the employee's control-layer key, Model ID = an allowed model. For Claude models, choose the **Anthropic** provider, tick "Use custom base URL" and enter `https://ai-gateway.example.com` with the same key. Cline's enterprise remote configuration can push these values.

## Aider

```bash
export OPENAI_API_BASE=https://ai-gateway.example.com/v1
export OPENAI_API_KEY=<control-layer key>
aider --model openai/qwen3:8b
```

The `openai/` prefix tells Aider the endpoint is OpenAI-compatible; the gateway sees the model as `qwen3:8b`.

## What to expect

- Streaming clients get the checked reply as a single chunk on `/v1/chat/completions`.
- A blocked prompt or tool result comes back as HTTP 403 with the policy reason; a withheld reply comes back as `[Response withheld by policy, request …]` with `finish_reason: content_filter`.
- These setups use API keys only; none of them carries a ChatGPT or claude.ai subscription login.
