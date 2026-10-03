import type { ActivityEvent } from "../api";
import { minutes, num, usd } from "./format";

const KIND_LABEL: Record<string, string> = {
  "check.input": "Prompt check",
  "check.output": "Response check",
  "check.tool_call": "Tool call check",
  "check.tool_result": "Tool result check",
  "check.tool_description": "Tool listing check",
  "lease.start": "Resource started",
  "lease.stop": "Resource stopped",
  "lease.flag": "Resource flagged",
  incident: "Incident",
  "usage.report": "Usage reported",
  "billing.charge": "Cloud charge",
  "cc.api_request": "Claude Code request",
  "cc.api_error": "Claude Code error",
  "cc.tool_decision": "Claude Code tool",
  "cc.tool_result": "Claude Code tool result",
  "cc.user_prompt": "Claude Code prompt",
  "metric.lines_of_code": "Lines of code",
  "metric.commit": "Commits",
  "metric.pull_request": "Pull request",
  "metric.active_time": "Active time",
  "metric.session": "Session",
  "metric.cost": "Cost",
  "metric.token": "Tokens",
};

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind;
}

function d<T = unknown>(e: ActivityEvent, k: string): T | undefined {
  return (e.detail as Record<string, T> | null)?.[k];
}

/** One line saying what happened, without content. */
export function describe(e: ActivityEvent): string {
  const value = d<number>(e, "value");
  switch (true) {
    case e.kind.startsWith("check."): {
      const target = e.tool ? `tool ${e.tool}` : e.model ? e.model : e.resource ?? "";
      const reason = d<string>(e, "reason");
      return reason ? `${target ? target + " · " : ""}${reason}` : target || "checked";
    }
    case e.kind === "lease.start":
      return `${e.resource ?? "resource"} started${e.tool ? ` via ${e.tool}` : ""}`;
    case e.kind === "lease.stop": {
      const m = d<number>(e, "minutes");
      return `${e.resource ?? "resource"} stopped${m ? ` after ${minutes(m)}` : ""}${d<string>(e, "reason") ? ` · ${d<string>(e, "reason")}` : ""}`;
    }
    case e.kind === "lease.flag": {
      const flags = d<string[]>(e, "flags") ?? [];
      const idle = d<number>(e, "idle_minutes");
      return `${e.resource ?? "resource"} flagged ${flags.join(", ")}${idle ? ` (idle ${minutes(idle)})` : ""}`;
    }
    case e.kind === "incident":
      return `${e.decision ?? "rule"}: ${d<string>(e, "detail") ?? ""}`;
    case e.kind === "usage.report":
      return `${e.resource ?? ""} ${num(d<number>(e, "quantity"), 1)} ${d<string>(e, "unit") ?? ""}`.trim();
    case e.kind === "billing.charge":
      return [d<string>(e, "ServiceName"), d<string>(e, "SubAccountName")].filter(Boolean).join(" · ") || (e.resource ?? "charge");
    case e.kind === "cc.api_request":
      return `${e.model ?? "model"}${e.decision && e.decision !== "ok" ? ` · ${e.decision}` : ""}`;
    case e.kind === "cc.tool_decision":
      return `${e.tool ?? "tool"} · ${e.decision ?? ""}`;
    case e.kind === "cc.user_prompt":
      return `prompt${d<number>(e, "prompt_length") ? ` (${d<number>(e, "prompt_length")} chars)` : ""}`;
    case e.kind === "metric.lines_of_code":
      return `${e.decision === "removed" ? "−" : "+"}${num(value)} lines ${e.decision ?? ""}`;
    case e.kind === "metric.commit":
      return `${num(value)} commit${value === 1 ? "" : "s"}`;
    case e.kind === "metric.pull_request":
      return `${num(value)} pull request${value === 1 ? "" : "s"}`;
    case e.kind === "metric.active_time":
      return `${minutes((value ?? 0) / 60)} active${e.decision ? ` (${e.decision})` : ""}`;
    default:
      if (value !== undefined) return `${e.kind.replace(/^metric\./, "")} ${num(value)}`;
      return e.tool ?? e.model ?? e.resource ?? "";
  }
}

export function costLine(e: ActivityEvent): string {
  const parts = [];
  if (e.usd) parts.push(usd(e.usd));
  if (e.tokens) parts.push(`${e.tokens.toLocaleString()} tok`);
  return parts.join(" · ");
}
