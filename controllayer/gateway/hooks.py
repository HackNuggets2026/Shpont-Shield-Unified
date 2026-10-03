"""Claude Code hooks: the same policy enforced inside Claude Code, before a prompt is sent or a tool runs.

  POST /v1/hooks/claude-code   UserPromptSubmit -> checked as a prompt (secrets, PII, signatures, budgets)
                               PreToolUse       -> checked as a tool call (role and workflow tool lists,
                                                   access grants, leases, secrets in tool input)
                               PostToolUse      -> opens/closes resource leases (argent simulators...)

Every hook also reports what telemetry does not: the session's permission mode (bypass mode is a
detection) and the MCP servers it calls (unapproved servers are a detection).

Only the deterministic tiers run here: hooks sit on the user's critical path. The reply uses Claude
Code's hook JSON: `decision: block` for prompts, `permissionDecision: deny` for tools; an allowed call
returns `{}` so Claude Code's own permission prompts still apply.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request

from .. import claude_code
from ..config import PolicyStore
from ..controls.access import authenticate
from ..engine import ControlLayer, flatten
from ..types import Action, Context, Direction, Verdict

MAX_TEXT = 200_000


def tool_name(name: str) -> tuple[str, str]:
    """(server, tool) for `mcp__server__tool`, else ("claude_code", name) for a built-in tool."""
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3:
            return parts[1], parts[2]
    return "claude_code", name


def register(
    app: FastAPI,
    store: PolicyStore,
    layer: ControlLayer,
    api_key: Callable[[Request], str | None],
    json_object: Callable[[Request], Any],
) -> None:
    seen: set[tuple[str, ...]] = set()  # (principal, session, what) already reported

    def report_once(who, session: str | None, kind: str, decision: str, severity: str, tool: str | None = None):
        key = (who.id, session or "", kind, decision)
        if key in seen:
            return
        if len(seen) > 100_000:
            seen.clear()
        seen.add(key)
        layer.ingestor.ingest(
            {
                "source": "claude_code",
                "kind": kind,
                "principal": who.id,
                "team": who.team,
                "session": session,
                "decision": decision,
                "tool": tool,
                "severity": severity,
                "client": "claude-code-hook",
            }
        )

    def refuse(event: str, reason: str) -> dict[str, Any]:
        reason = f"Shpont Shield: {reason}"
        if event == "PreToolUse":
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        return {"decision": "block", "reason": reason}

    def explain(v: Verdict) -> str:
        if v.action is Action.REDACT:
            return f"remove the sensitive data first ({v.reason})"
        return v.reason

    @app.post("/v1/hooks/claude-code")
    async def claude_code_hook(request: Request):
        body = await json_object(request)
        event = str(body.get("hook_event_name") or "")
        p = store.policy
        who = authenticate(p, api_key(request))
        if not who.authenticated:
            if p.identity.require_auth and event in ("UserPromptSubmit", "PreToolUse"):
                return refuse(event, "no valid API key in the hook configuration (SHIELD_KEY)")
            return {}
        h = request.headers
        session = str(body.get("session_id") or "")[:128] or None
        mode = str(body.get("permission_mode") or "")
        if mode:
            bypass = mode.lower() in claude_code.BYPASS_MODES
            report_once(who, session, "cc.permission_mode_changed", mode, "medium" if bypass else "info")
        wf = (h.get("x-acl-workflow") or "").strip() or None
        base = {
            "channel": "claude_code",
            "workflow": wf,
            "workflow_source": "declared" if wf else None,
            "task_id": (h.get("x-acl-task") or "").strip() or None,
            "session_id": session,
            # No client: hooks run beside telemetry and the gateway, so they would look like a "new client".
        }

        if event == "UserPromptSubmit":
            text = str(body.get("prompt") or body.get("prompt_text") or "")[:MAX_TEXT]
            ctx = layer.attribute(Context(who, Direction.INPUT, text, **base))
            v = await layer.evaluate(ctx, semantic=False)
            if v.action in (Action.BLOCK, Action.REDACT):
                return refuse(event, explain(v))
            if v.action is Action.WARN:
                return {"systemMessage": f"Shpont Shield: {v.reason}"}
            return {}

        if event in ("PreToolUse", "PostToolUse"):
            server, tool = tool_name(str(body.get("tool_name") or ""))
            args = body.get("tool_input") if isinstance(body.get("tool_input"), dict) else {}
            if server != "claude_code":
                report_once(who, session, "cc.mcp_server_connection", "used", "info", tool=server)
            if event == "PostToolUse":
                ctx = layer.attribute(Context(who, Direction.TOOL_CALL, "", tool=tool, tool_args=args, **base))
                result = flatten(body.get("tool_response"))[:MAX_TEXT]
                layer.leases.after_call(ctx, p, server, args, result)
                return {}
            text = flatten(args)[:MAX_TEXT]
            ctx = layer.attribute(Context(who, Direction.TOOL_CALL, text, tool=tool, tool_args=args, **base))
            v = await layer.evaluate(ctx, semantic=False)
            if v.action in (Action.BLOCK, Action.REDACT):
                return refuse(event, explain(v))
            return {}

        return {}
