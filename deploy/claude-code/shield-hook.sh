#!/bin/sh
# Fail-closed Claude Code hook: forwards the hook input to Shpont Shield and blocks (exit 2) when the
# gateway cannot be reached. HTTP hooks fail open; use this one where an unreachable gateway must stop work.
out=$(curl -sS --fail --max-time 8 \
  -H "Authorization: Bearer ${SHIELD_KEY}" \
  -H "x-acl-workflow: ${SHIELD_WORKFLOW:-}" -H "x-acl-task: ${SHIELD_TASK:-}" \
  -H "content-type: application/json" --data-binary @- \
  "${SHIELD_URL:-http://127.0.0.1:8787}/v1/hooks/claude-code") || {
  echo "Shpont Shield is unreachable at ${SHIELD_URL:-http://127.0.0.1:8787}; blocked by policy (fail closed)." >&2
  exit 2
}
printf '%s' "$out"
