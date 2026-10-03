// Security console helpers: human rule names, detection policy, and the incident list with history.
import { admin, get, type AdminAction, type Incident } from "../api";
import { titleCase } from "./format";

export interface RuleMeta {
  label: string;
  /** What the rule looks for, in one line. */
  what: string;
  /** Rules fed by Claude Code telemetry rather than the gateway. */
  cc?: boolean;
}

export const RULES: Record<string, RuleMeta> = {
  exfiltration: { label: "Data exfiltration", what: "Prompts trying to move confidential data out, worse with a usage spike" },
  probing: { label: "Probing", what: "Repeated blocked attempts in a short window: someone testing the guardrails" },
  secret_paste: { label: "Secret pasted", what: "Credentials sent to AI tools again and again" },
  usage_spike: { label: "Usage spike", what: "Token volume far above this person's own weekly baseline" },
  new_client: { label: "New client", what: "The key is suddenly used from a client it has never used: a possible stolen key" },
  tool_drift: { label: "Tool drift", what: "First use of a sensitive tool (email, HTTP, delete, transfer)" },
  off_hours: { label: "Off-hours activity", what: "Activity outside the usual working hours" },
  zombie_resource: { label: "Zombie resource", what: "A leased resource left running idle and billing" },
  unlabeled_resource: { label: "Unlabeled resource", what: "A resource leased outside any approved workflow" },
  permission_bypass: { label: "Claude Code bypass mode", what: "Claude Code switched to a mode that skips permission prompts", cc: true },
  unapproved_mcp_server: { label: "Unapproved MCP server", what: "Claude Code connected to an MCP server that is not on the approved list", cc: true },
  rejected_edit_storm: { label: "Rejected edit storm", what: "Many Claude Code tool calls rejected in a few minutes", cc: true },
};

export const ruleLabel = (rule: string): string => RULES[rule]?.label ?? titleCase(rule);
export const ruleWhat = (rule: string): string | undefined => RULES[rule]?.what;

export const STATUS_RANK: Record<string, number> = { open: 0, acknowledged: 1, resolved: 2, dismissed: 3 };
export const SEVERITY_RANK: Record<string, number> = { high: 0, medium: 1, low: 2, info: 3 };

/** Open first, then acknowledged, then closed; newest first within each. */
export function triageOrder(a: Incident, b: Incident): number {
  return (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9) || b.ts - a.ts;
}

// ---- detection policy ----------------------------------------------------------------------------

export interface DetectionPolicy {
  enabled: boolean;
  half_life_minutes: number;
  response: { auto: boolean; alert: number; tighten: number; quarantine: number; tighten_budget_scale: number };
  rules: Record<string, { enabled: boolean; weight: number; window_minutes: number; count: number; tools: string[] }>;
}

export const DEFAULT_THRESHOLDS = { alert: 30, tighten: 60, quarantine: 80 };

export async function detectionPolicy(): Promise<DetectionPolicy | null> {
  const r = await get<{ policy?: { detections?: DetectionPolicy } }>("/api/admin/policy");
  return r.policy?.detections ?? null;
}

// ---- incidents, including history the live list leaves out ---------------------------------------

export type SecIncident = Incident & {
  /** Older than the risk engine's 7-day window: listed from the activity log, never counts toward risk. */
  archived?: boolean;
};

/**
 * `/admin/incidents` only lists the risk engine's in-memory window (7 days). Older incidents are still in
 * the store and readable by id, and every incident left a `detections/incident` event, so we find them
 * there and fetch each one for its current status.
 */
export async function incidentsWithHistory(): Promise<{ incidents: SecIncident[]; scores: Record<string, number>; levels: string[] }> {
  const [live, events] = await Promise.all([admin.incidents(), admin.activity({ source: "detections", kind: "incident", limit: 500 }).catch(() => [])]);
  const known = new Set(live.incidents.map((i) => i.id));
  const older = events
    .map((e) => (e.detail as { incident?: string } | null)?.incident)
    .filter((id): id is string => !!id && !known.has(id));
  const unique = [...new Set(older)].slice(0, 60);
  const fetched = await Promise.all(unique.map((id) => admin.incident(id).then((d) => d.incident).catch(() => null)));
  const archived = fetched.filter((i): i is Incident => !!i).map((i) => ({ ...i, archived: true }));
  return { ...live, incidents: [...live.incidents, ...archived] };
}

/** Auto responses are logged by actors named `auto:<engine>`. */
export const isAuto = (a: AdminAction) => a.actor.startsWith("auto:");

/** What an admin action did, in a few words. */
export function responseLabel(a: AdminAction): string {
  const d = (a.detail ?? {}) as Record<string, unknown>;
  if (a.action === "tighten" || typeof d.budget_scale === "number")
    return typeof d.budget_scale === "number" ? `Budget cut to ${Math.round(d.budget_scale * 100)}%` : "Budget tightened";
  if (a.action === "quarantine" || d.status === "quarantined") return "Quarantined";
  if (a.action === "revoke" || d.status === "revoked") return "Key revoked";
  if (a.action === "alert") return "Alert raised";
  if (a.action === "view_events") return "Viewed content";
  if (a.action === "clear_restrictions") return "Restrictions lifted";
  if (a.action.startsWith("incident_")) return `Incident ${a.action.slice(9)}`;
  return titleCase(a.action);
}
