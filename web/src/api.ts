// Typed client for the Shpont Shield gateway JSON API (/api/admin/*, /api/me/*, /api/session).

import { PREVIEW, previewGet } from "./lib/preview";

export type Role = "admin" | "employee";

export interface AdminSession {
  role: "admin";
  name: string;
}
export interface EmployeeSession {
  role: "employee";
  principal: string;
  team: string;
  job_role: string;
  email: string | null;
  status: string;
}
export type Session = AdminSession | EmployeeSession;

export interface Credentials {
  secret: string;
  role: Role;
  adminUser?: string;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let creds: Credentials | null = null;
let onUnauthorized: (() => void) | null = null;

export function setCredentials(c: Credentials | null) {
  creds = c;
}
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

function authHeaders(c: Credentials | null): Record<string, string> {
  if (!c) return {};
  if (c.role === "admin") {
    const h: Record<string, string> = { "x-admin-token": c.secret };
    if (c.adminUser) h["x-admin-user"] = c.adminUser;
    return h;
  }
  return { Authorization: `Bearer ${c.secret}` };
}

function errorMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "error" in body) {
    const e = (body as { error: unknown }).error;
    if (typeof e === "string") return e;
    if (e && typeof e === "object" && "message" in e) return String((e as { message: unknown }).message);
  }
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    return typeof d === "string" ? d : JSON.stringify(d);
  }
  return fallback;
}

async function request<T>(method: string, path: string, body?: unknown, c: Credentials | null = creds): Promise<T> {
  if (PREVIEW) {
    if (method !== "GET") throw new ApiError(403, "This is a read-only preview, so changes are switched off. Run the project locally to try them.");
    const hit = await previewGet(c?.secret ?? "", path);
    if (hit.ok) return hit.data as T;
    if (path.startsWith("/api/session")) throw new ApiError(401, "unknown key");
    throw new ApiError(404, "Not part of the preview snapshot. Run the project locally to see everything.");
  }
  const headers: Record<string, string> = { accept: "application/json", ...authHeaders(c) };
  if (body !== undefined) headers["content-type"] = "application/json";
  let resp: Response;
  try {
    resp = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    throw new ApiError(0, `cannot reach the gateway (${e instanceof Error ? e.message : String(e)})`);
  }
  const text = await resp.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!resp.ok) {
    if (resp.status === 401 && c === creds && onUnauthorized) onUnauthorized();
    throw new ApiError(resp.status, errorMessage(data, `${resp.status} ${resp.statusText}`));
  }
  return data as T;
}

export const get = <T>(path: string) => request<T>("GET", path);
export const post = <T>(path: string, body: unknown) => request<T>("POST", path, body);

function qs(params: Record<string, string | number | undefined | null>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : "";
}

/** Who holds this secret: tries it as the admin token, then as an employee API key. */
export async function probeSession(secret: string, adminUser?: string): Promise<{ session: Session; creds: Credentials }> {
  const asAdmin: Credentials = { secret, role: "admin", adminUser };
  try {
    const s = await request<Session>("GET", "/api/session", undefined, asAdmin);
    if (s.role === "admin") return { session: s, creds: asAdmin };
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 401) throw e;
  }
  const asEmployee: Credentials = { secret, role: "employee" };
  const s = await request<Session>("GET", "/api/session", undefined, asEmployee);
  return { session: s, creds: s.role === "admin" ? asAdmin : asEmployee };
}

// ---- shared shapes ---------------------------------------------------------------------------

export type Severity = "info" | "low" | "medium" | "high";
export type Decision = "allow" | "log" | "warn" | "redact" | "block" | string;

export interface SpendTriple {
  today: number;
  month_to_date: number;
  month_forecast: number;
}

export interface Overview {
  spend: SpendTriple;
  teams: Record<string, SpendTriple>;
  global_usd_per_day: number | null;
  guard_tokens_today: number;
  leases_open: number;
  leases_running_usd: number;
  zombies: number;
  incidents_open: number;
  requests_pending: number;
  at_risk: number;
  /** Enterprise scale (docs/scale-contract.md). */
  headcount?: number;
  active?: number;
  org_name?: string;
}

export interface Timeseries {
  metric: string;
  by: string | null;
  days: string[];
  series: Record<string, number[]>;
  totals: number[];
}

export interface AdherenceRow {
  key: string;
  total: number;
  allow: number;
  log: number;
  warn: number;
  redact: number;
  block: number;
  adherence: number | null;
}
export interface Adherence {
  by: string | null;
  rows: AdherenceRow[];
  overall: AdherenceRow | null;
}

export interface Measured {
  runs: number;
  usd_p50: number | null;
  usd_p90: number | null;
  tokens_p50: number | null;
  tokens_p90: number | null;
  minutes_p50: number | null;
  usd_mean: number | null;
}

export interface Workflow {
  name: string;
  description: string;
  tier: string;
  enabled: boolean;
  approval: string;
  teams: string[];
  roles: string[];
  models: string[];
  tools: string[];
  resources: Record<string, { max_concurrent: number | null; max_minutes: number | null }>;
  per_run: { tokens: number | null; usd: number | null };
  measured: Measured;
  available?: boolean;
  why?: string | null;
}
export interface Menu {
  require_label: boolean;
  classify_unlabeled: boolean;
  workflows: Workflow[];
}

export interface PrincipalRow {
  principal: string;
  team: string;
  role: string;
  status: string;
  budget_scale: number;
  approved_workflows: string[];
  reason: string;
  by: string;
  since: number | null;
  risk: number;
  level: string;
  open_incidents: number;
  today: { requests: number; tokens: number; usd: number };
  leases: number;
}

export interface Breakdown {
  requests: number;
  tokens: number;
  usd: number;
  minutes: number;
  guard_tokens: number;
  [dim: string]: string | number | null;
}

export interface Incident {
  id: string;
  ts: number;
  principal: string;
  rule: string;
  severity: Severity;
  weight: number;
  detail: string;
  evidence: string[];
  status: "open" | "acknowledged" | "resolved" | "dismissed";
  note: string;
}

export interface Grant {
  id: string;
  resource: string;
  locations?: string[];
  actions: string[];
  workflow: string | null;
  expires: number | null;
  granted_by: string;
  reason: string;
  granted_at: number | null;
  live: boolean;
  principal?: string;
  title?: string;
  minutes_left?: number | null;
}

export interface Lease {
  id: string;
  resource: string;
  handle: string | null;
  server: string | null;
  principal: string;
  team: string;
  workflow: string | null;
  task: string | null;
  tool: string | null;
  started: number;
  last_activity: number;
  ended: number | null;
  end_reason: string | null;
  usd: number;
  flags: string[];
  minutes?: number;
  idle_minutes?: number;
  running_usd?: number;
}

export interface AccessRequest {
  id: string;
  ts: number;
  principal: string;
  kind: "workflow" | "quota" | "grant";
  workflow: string | null;
  scale: number | null;
  reason: string;
  status: "pending" | "approved" | "denied";
  decided_by: string | null;
  decided_at: number | null;
  note: string;
  detail: { resource?: string; minutes?: number; actions?: string[]; workflow?: string } | null;
}

export interface AdminAction {
  ts: number;
  actor: string;
  action: string;
  target: string;
  reason: string;
  detail: unknown;
}

export interface ActivityEvent {
  ts: number;
  id: string;
  source: string;
  kind: string;
  principal: string | null;
  team: string | null;
  client: string | null;
  session: string | null;
  prompt_id?: string | null;
  task: string | null;
  workflow: string | null;
  resource: string | null;
  urn: string | null;
  model: string | null;
  tool: string | null;
  decision: string | null;
  severity: Severity | null;
  usd: number | null;
  tokens: number | null;
  request_id: string | null;
  detail: Record<string, unknown> | null;
  evidence?: boolean;
}

export interface AuditFinding {
  control: string;
  category: string;
  action: string;
  detail: string;
  score?: number;
  tier?: string;
  shadow?: boolean;
}
export interface AuditEvent {
  ts: number;
  request_id: string;
  channel: string;
  direction: string;
  principal: string;
  team: string;
  model: string | null;
  tool: string | null;
  action: string;
  status_code: number;
  reason: string;
  text: string | null;
  findings: AuditFinding[];
  workflow?: string | null;
  usd?: number;
  tokens?: number;
}

export interface IncidentDetail {
  incident: Incident;
  timeline: ActivityEvent[];
  principal: { id: string; risk: number; level: string; status: string };
  actions: AdminAction[];
}

export interface Person extends Omit<PrincipalRow, "leases"> {
  email: string | null;
  spend: Timeseries;
  by_workflow: Breakdown[];
  by_resource: Breakdown[];
  by_source: Breakdown[];
  adherence: AdherenceRow | null;
  incidents: Incident[];
  grants: Grant[];
  leases: Lease[];
  requests: AccessRequest[];
  admin_actions: AdminAction[];
}

export interface CatalogItem {
  name: string;
  class: "consumable" | "leasable" | "access_grant";
  urn: string;
  title: string;
  description: string;
  category: string;
  provider: string;
  unit: string;
  price: { usd_per_unit: number; usd_per_1m_input: number; usd_per_1m_output: number; usd_per_compute_second: number };
  meter: string;
  models: string[];
  tools: string[];
  actions: string[];
  grant?: { approval: string; max_minutes: number };
  lease?: { max_concurrent_per_principal: number | null; idle_minutes: number | null; auto_reclaim: boolean };
  sensitivity: string;
  owner: string;
  usage: { usd: number; tokens: number; minutes: number; requests: number };
  live_leases: number;
  live_grants: number;
  workflows: string[];
}
export interface Catalog {
  classes: { consumable: CatalogItem[]; leasable: CatalogItem[]; access_grant: CatalogItem[] };
  uncatalogued: { resource: string; usd: number }[];
}

export interface Ok {
  ok: boolean;
  version?: string;
}

// ---- admin ------------------------------------------------------------------------------------

const A = "/api/admin";
/** A trap (policy `decoys`) and the people who touched it, newest first. */
export interface DecoyRow {
  name: string;
  title: string;
  kind: "document" | "dataset" | "credential" | "system";
  urn: string;
  planted_in: string;
  identifiers: string[];
  touches: number;
  people: number;
  /** Touches still open or acknowledged. */
  open: number;
  recent: TrapTouch[];
}

export interface TrapTouch {
  id: string;
  ts: number;
  principal: string;
  name?: string | null;
  team?: string | null;
  department?: string | null;
  rule: string;
  severity: string;
  status: string;
  detail: string;
  /** The decoy's name (events list only). */
  trap?: string | null;
}

export interface Decoys {
  decoys: DecoyRow[];
  touches: number;
  open: number;
  /** People who opened a trap (not just asked for one). */
  caught: number;
  events: TrapTouch[];
}

export const admin = {
  overview: () => get<Overview>(`${A}/overview`),
  decoys: () => get<Decoys>(`${A}/decoys`),
  timeseries: (by: string, days = 30, metric = "usd") => get<Timeseries>(`${A}/timeseries${qs({ metric, by, days })}`),
  adherence: (by: string, days = 30) => get<Adherence>(`${A}/adherence${qs({ by, days })}`),
  usage: (by: string, days = 30) => get<Breakdown[]>(`${A}/usage${qs({ by, days })}`),
  menu: () => get<Menu>(`${A}/menu`),
  editWorkflow: (name: string, patch: Record<string, unknown>, reason: string) =>
    post<Ok>(`${A}/menu/${encodeURIComponent(name)}`, { ...patch, reason }),
  principals: () => get<PrincipalRow[]>(`${A}/principals`),
  restrict: (pid: string, body: Record<string, unknown>) => post<Ok>(`${A}/principals/${encodeURIComponent(pid)}`, body),
  person: (pid: string, days = 30) => get<Person>(`${A}/people/${encodeURIComponent(pid)}${qs({ days })}`),
  personEvents: (pid: string, reason: string, limit = 100) =>
    get<AuditEvent[]>(`${A}/principals/${encodeURIComponent(pid)}/events${qs({ reason, limit })}`),
  incidents: (status?: string) =>
    get<{ incidents: Incident[]; scores: Record<string, number>; levels: string[] }>(`${A}/incidents${qs({ status })}`),
  incident: (id: string) => get<IncidentDetail>(`${A}/incidents/${encodeURIComponent(id)}`),
  setIncident: (id: string, status: string, note: string) =>
    post<Ok>(`${A}/incidents/${encodeURIComponent(id)}`, { status, note }),
  activity: (p: { limit?: number; before?: number; source?: string; kind?: string; principal?: string; severity?: string }) =>
    get<ActivityEvent[]>(`${A}/activity${qs(p)}`),
  catalog: () => get<Catalog>(`${A}/catalog`),
  grants: () => get<Grant[]>(`${A}/grants`),
  grant: (pid: string, body: { resource: string; minutes: number; actions?: string[]; workflow?: string; reason: string }) =>
    post<{ ok: boolean; grant: Grant }>(`${A}/principals/${encodeURIComponent(pid)}/grants`, body),
  revokeGrant: (pid: string, gid: string, reason: string) =>
    post<Ok>(`${A}/principals/${encodeURIComponent(pid)}/grants/${encodeURIComponent(gid)}/revoke`, { reason }),
  leases: () => get<{ open: Lease[]; recent: Lease[] }>(`${A}/leases`),
  releaseLease: (id: string) => post<{ ok: boolean; stopped: boolean }>(`${A}/leases/${encodeURIComponent(id)}/release`, {}),
  requests: (status?: string) => get<AccessRequest[]>(`${A}/requests${qs({ status })}`),
  decide: (id: string, decision: "approve" | "deny", note: string) =>
    post<Ok>(`${A}/requests/${encodeURIComponent(id)}`, { decision, note }),
  actions: (target?: string) => get<AdminAction[]>(`${A}/actions${qs({ target })}`),
};

// ---- playground ---------------------------------------------------------------------------

export type TryDirection = "input" | "output" | "tool_call" | "tool_result" | "tool_description";

export interface TryRequest {
  principal: string;
  direction: TryDirection;
  text?: string;
  model?: string;
  tool?: string;
  arguments?: Record<string, unknown>;
}

export interface TryFinding {
  control: string;
  category: string;
  action: Decision;
  proposed: Decision;
  tier: string;
  score: number;
  detail: string;
  shadow: boolean;
}

/** POST /admin/try: the guard's verdict for one message, as that person, unmetered (channel dashboard). */
export interface TryResult {
  request_id: string;
  action: Decision;
  reason: string;
  /** What would be forwarded (redactions applied); empty when refused. */
  text: string;
  policy_version: string;
  /** Milliseconds per stage: gates, budget, deterministic (control:secrets, control:pii, control:signatures), semantic... total. */
  latency_ms: Record<string, number>;
  findings: TryFinding[];
  /** The HTTP status the gateway answered with (403/429/401 when refused). */
  status: number;
}

/** A refusal is still a verdict, so this resolves for any status that carries one (and never signs the admin out). */
export async function tryAs(body: TryRequest): Promise<TryResult> {
  if (PREVIEW) throw new ApiError(403, "The playground needs the gateway; run the project locally to try prompts.");
  let resp: Response;
  try {
    resp = await fetch(`${A}/try`, {
      method: "POST",
      headers: { accept: "application/json", "content-type": "application/json", ...authHeaders(creds) },
      body: JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError(0, `cannot reach the gateway (${e instanceof Error ? e.message : String(e)})`);
  }
  const data: unknown = await resp.json().catch(() => null);
  if (data && typeof data === "object" && "action" in data) return { ...(data as Omit<TryResult, "status">), status: resp.status };
  throw new ApiError(resp.status, errorMessage(data, `${resp.status} ${resp.statusText}`));
}

/** Downloads an admin export (needs the token header, so it cannot be a plain link). */
export async function downloadExport(path: string, filename: string) {
  if (PREVIEW) throw new ApiError(403, "Exports need the gateway; run the project locally to download them.");
  const resp = await fetch(path, { headers: authHeaders(creds) });
  if (!resp.ok) {
    let body: unknown = null;
    try {
      body = await resp.json();
    } catch {
      /* not JSON */
    }
    throw new ApiError(resp.status, errorMessage(body, `${resp.status} ${resp.statusText}`));
  }
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---- employee ---------------------------------------------------------------------------------

export interface BudgetScope {
  scope: string;
  key: string;
  requests: number;
  tokens: number;
  usd: number;
  compute_seconds: number;
  tokens_limit: number | null;
  usd_limit: number | null;
}

export interface MeSummary {
  principal: string;
  team: string;
  role: string;
  status: {
    status: string;
    budget_scale: number;
    reason: string;
    by: string;
    since: number | null;
    approved_workflows: string[];
  };
  grants: Grant[];
  budgets: BudgetScope[];
  spend: { today: number; month_to_date: number };
  by_workflow: Breakdown[];
  by_resource: Breakdown[];
  by_model: Breakdown[];
  runs: Breakdown[];
  leases: Lease[];
  menu: Workflow[];
  events: AuditEvent[];
  requests: AccessRequest[];
  admin_activity: AdminAction[];
  tips: string[];
  collected: string[];
  risk?: { score: number; level: string; incidents: Incident[] };
}

export interface Requestable {
  name: string;
  title: string;
  urn: string;
  actions: string[];
  sensitivity: string;
  approval: string;
  max_minutes: number | null;
}

const M = "/api/me";
export const me = {
  summary: () => get<MeSummary>(`${M}/summary`),
  timeseries: (by = "workflow", days = 30, metric = "usd") => get<Timeseries>(`${M}/timeseries${qs({ metric, by, days })}`),
  activity: (limit = 50, before?: number) => get<ActivityEvent[]>(`${M}/activity${qs({ limit, before })}`),
  grants: () => get<{ grants: Grant[]; requestable: Requestable[] }>(`${M}/grants`),
  request: (body: {
    kind: "workflow" | "quota" | "grant";
    workflow?: string;
    scale?: number;
    resource?: string;
    minutes?: number;
    reason: string;
  }) => post<{ ok: boolean; id: string }>(`${M}/requests`, body),
  releaseLease: (id: string) => post<{ ok: boolean; stopped: boolean }>(`${M}/leases/${encodeURIComponent(id)}/release`, {}),
};

// ---- guardrail controls (console Controls page) ---------------------------------------------------

export type ControlMode = "allow" | "log" | "warn" | "redact" | "block";
export type ControlProfile = "strict" | "balanced" | "permissive";

export interface ControlRow {
  name: string;
  kind: "deterministic" | "context" | "semantic";
  enabled: boolean;
  mode: ControlMode;
  shadow: boolean;
  hits: number;
  /** The main probability line (`min_score` for the PII model); null when the control has none. */
  threshold: { action: string; value: number } | null;
  description: string | null;
  categories: [string, number][];
}

export interface LatencyStat {
  p50: number;
  p95: number;
  max: number;
  count: number;
}

export interface ControlsSummary {
  policy: { name: string; version: string; reloads: number; last_error: string | null };
  feed: { version: string | null; signatures: number; loaded_at: number | null; errors: string[] };
  semantic: { backend: "ollama" | "heuristic" | "off"; fast_model: string; deep_model: string | null; fail_mode: "open" | "closed" };
  controls: ControlRow[];
  top_categories: [string, number][];
  shadow_would_have: [string, number][];
  latency_ms: Record<string, LatencyStat>;
}

export const controlsApi = {
  summary: () => get<ControlsSummary>(`${A}/summary`),
  edit: (name: string, body: Partial<{ enabled: boolean; mode: ControlMode; shadow: boolean; threshold: number }>) =>
    request<Ok>("PATCH", `${A}/controls/${encodeURIComponent(name)}`, body),
  profile: () => get<{ profiles: ControlProfile[]; active: ControlProfile | null }>(`${A}/controls/profile`),
  setProfile: (profile: ControlProfile) => post<Ok>(`${A}/controls/profile`, { profile }),
};
