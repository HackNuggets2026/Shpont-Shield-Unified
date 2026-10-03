// Typed client for the enterprise-scale endpoints (docs/scale-contract.md): the org model, org units,
// paginated people search, outliers, and the department/team filters added to existing endpoints.
// Kept beside api.ts so the shared client only grows by addition.
import { get, type ActivityEvent, type Adherence, type Breakdown, type Incident, type PrincipalRow, type Timeseries } from "./api";

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "" && v !== false) u.set(k, String(v === true ? 1 : v));
  const s = u.toString();
  return s ? `?${s}` : "";
}

/** The metric block shared by the org totals, department rows and team rows. */
export interface UnitMetrics {
  headcount: number;
  active: number;
  usd: number;
  usd_prev: number;
  usd_per_active: number | null;
  tokens: number;
  checks: number;
  interventions: number;
  adherence: number | null;
  incidents_open: number;
  people_at_risk: number;
  claude_code_users: number;
  claude_code_usd: number;
}

export interface UnitRow extends UnitMetrics {
  name: string;
  /** Departments carry an owner and a team count; teams carry their department. */
  owner?: string;
  teams?: number;
  department?: string;
  top_workflow?: string | null;
}

export interface Org {
  name: string;
  headcount: number;
  active: number;
  teams: number;
  departments_count: number;
  window: { days: number; since: number };
  totals: UnitMetrics;
  departments: UnitRow[];
}

export interface Page<T> {
  total: number;
  rows: T[];
}

export type TeamSort = "usd" | "usd_per_active" | "adherence" | "risk" | "headcount";
export type PeopleSort = "risk" | "usd" | "tokens" | "name";
export type Order = "asc" | "desc";

export interface CostOutlier {
  principal: string;
  name: string;
  team: string;
  department: string;
  usd: number;
  team_median: number;
  ratio: number;
}
export interface RiskOutlier {
  principal: string;
  name: string;
  team: string;
  department: string;
  risk: number;
  level: string;
  status: string;
  open_incidents: number;
}
export interface GrowthOutlier {
  principal: string;
  name?: string;
  team: string;
  department: string;
  usd: number;
  usd_prev: number;
  growth: number;
}
export interface Outliers {
  cost: CostOutlier[];
  risk: RiskOutlier[];
  growth: GrowthOutlier[];
}

export interface UnitWorkflow {
  workflow: string;
  usd: number;
  runs: number;
  p50: number | null;
  p90: number | null;
}
export interface UnitResource {
  resource: string;
  usd: number;
  tokens: number;
  minutes: number;
}

export interface OrgUnit {
  kind: "department" | "team";
  name: string;
  /** Teams also name their department here (or in metrics.department). */
  department?: string;
  metrics: UnitRow;
  spend: { days: string[]; series: Record<string, number[]>; totals: number[] };
  adherence_trend: { days: string[]; values: (number | null)[] };
  by_workflow: UnitWorkflow[];
  by_resource: UnitResource[];
  teams: UnitRow[];
  outliers: { cost: CostOutlier[]; risk: RiskOutlier[] };
}

export interface PersonRow extends PrincipalRow {
  department: string;
  name: string;
  email: string | null;
  usd: number;
  tokens: number;
}

export type PersonStatus = "active" | "quarantined" | "revoked" | "limited";

export interface PeopleQuery {
  q?: string;
  department?: string;
  team?: string;
  status?: string;
  sort?: PeopleSort;
  order?: Order;
  limit?: number;
  offset?: number;
  days?: number;
}

export interface ValueRow {
  key: string;
  usd: number;
  claude_code_usd: number;
  commits: number;
  pull_requests: number;
  lines_added: number;
  lines_removed: number;
  sessions: number;
  usd_per_commit: number | null;
  lines_per_usd: number | null;
}

/** Incidents gain who/where at scale. */
export interface OrgIncident extends Incident {
  department?: string;
  team?: string;
  name?: string;
}

export interface UnitFilter {
  department?: string;
  team?: string;
}

const A = "/api/admin";

export const org = {
  org: (days = 30) => get<Org>(`${A}/org${qs({ days })}`),
  teams: (p: { department?: string; days?: number; sort?: TeamSort; order?: Order; limit?: number; offset?: number } = {}) =>
    get<Page<UnitRow>>(`${A}/org/teams${qs({ days: 30, limit: 100, ...p })}`),
  unit: (kind: "department" | "team", name: string, days = 30) => get<OrgUnit>(`${A}/org/unit${qs({ kind, name, days })}`),
  people: (p: PeopleQuery) => get<Page<PersonRow>>(`${A}/people${qs({ limit: 50, days: 30, ...p })}`),
  outliers: (p: { days?: number; limit?: number } & UnitFilter = {}) => get<Outliers>(`${A}/outliers${qs({ days: 7, limit: 10, ...p })}`),
  incidents: (p: { status?: string; limit?: number; offset?: number; department?: string; team?: string; rule?: string } = {}) =>
    get<{ incidents: OrgIncident[]; total?: number; scores: Record<string, number> }>(`${A}/incidents${qs(p)}`),
  /** Existing endpoints with the scale filters (department=, team=) and by=department. */
  timeseries: (by: string, days = 30, f: UnitFilter = {}, metric = "usd") =>
    get<Timeseries>(`${A}/timeseries${qs({ metric, by, days, ...f })}`),
  adherence: (by: string, days = 30, f: UnitFilter = {}) => get<Adherence>(`${A}/adherence${qs({ by, days, ...f })}`),
  usage: (by: string, days = 30, f: UnitFilter = {}) => get<Breakdown[]>(`${A}/usage${qs({ by, days, ...f })}`),
  value: (by: string, days = 30, f: UnitFilter = {}) =>
    get<{ by: string; days: number; rows: ValueRow[] }>(`${A}/value${qs({ by, days, ...f })}`),
  activity: (p: { limit?: number; before?: number; source?: string; kind?: string; principal?: string; severity?: string; department?: string; interesting?: boolean }) =>
    get<(ActivityEvent & { department?: string | null })[]>(`${A}/activity${qs(p)}`),
};

/** Org-unit routes, so every link into the hierarchy is spelled the same way. */
export const orgPath = {
  root: "/console/org",
  department: (name: string) => `/console/org/department/${encodeURIComponent(name)}`,
  team: (name: string) => `/console/org/team/${encodeURIComponent(name)}`,
  person: (pid: string) => `/console/people/${encodeURIComponent(pid)}`,
  people: (p: { q?: string; department?: string; team?: string; status?: string; sort?: string } = {}) => {
    const s = qs(p);
    return `/console/people${s}`;
  },
};
