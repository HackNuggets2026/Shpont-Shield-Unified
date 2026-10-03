// Typed client for the enterprise-scale operational endpoints (docs/scale-contract.md): incidents, leases,
// grants and requests, paginated and filtered server-side, plus their org-level summaries.
// Lives beside api.ts (which it reuses) so the shared client only ever grows.
import { get, type AccessRequest, type ActivityEvent, type Breakdown, type Grant, type Incident, type Lease } from "./api";

const A = "/api/admin";

type Params = Record<string, string | number | boolean | undefined | null>;
function qs(params: Params): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    u.set(k, v === true ? "1" : String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

/** Who a row is about, as the directory knows them. */
export interface OrgFields {
  department?: string | null;
  team?: string | null;
  name?: string | null;
}

export interface Page<T> {
  rows: T[];
  /** Matching rows in total. Null when the server does not say (then `hasMore` is a guess). */
  total: number | null;
  hasMore: boolean;
}

// ---- incidents -----------------------------------------------------------------------------------

export type OrgIncident = Incident & OrgFields & { scored?: boolean };

export interface IncidentFilters {
  /** `active` means open or acknowledged ("needs attention"). */
  status?: "active" | "open" | "acknowledged" | "resolved" | "dismissed" | "";
  severity?: string;
  rule?: string;
  department?: string;
  team?: string;
  principal?: string;
  /** Free-text person search (id, name or email). */
  q?: string;
  days?: number;
  /** `severity` (default): high first, then open before acknowledged, then newest. `ts`: newest first. */
  sort?: "severity" | "ts";
  limit?: number;
  offset?: number;
}

export interface IncidentsPage extends Page<OrgIncident> {
  scores: Record<string, number>;
}

export interface IncidentSummary {
  by_rule: { rule: string; open: number; acknowledged: number; resolved: number; dismissed: number; total: number }[];
  by_department: { department: string; open: number; total: number; people_at_risk: number }[];
  trend: { days: string[]; opened: number[]; closed: number[] };
  auto_actions_24h: number;
  /** Not in the contract yet: open incidents per severity. */
  by_severity?: Record<string, number>;
  /** Not in the contract yet: rule × department counts for the heatmap. */
  by_rule_department?: { rule: string; department: string; open: number; total: number }[];
}

// ---- leases --------------------------------------------------------------------------------------

export type OrgLease = Lease & OrgFields;
export interface LeaseRollup {
  open: number;
  zombies: number;
  running_usd: number;
}
export interface LeasesResponse {
  open: OrgLease[];
  recent: OrgLease[];
  open_total?: number;
  summary?: {
    by_resource: (LeaseRollup & { resource: string })[];
    by_department: (LeaseRollup & { department: string })[];
  };
}
export interface LeaseFilters {
  department?: string;
  resource?: string;
  zombies?: boolean;
  limit?: number;
  offset?: number;
}

// ---- grants --------------------------------------------------------------------------------------

export type OrgGrant = Grant & OrgFields;
export interface GrantSummary {
  live: number;
  expiring_1h: number;
  by_resource: { resource: string; live: number }[];
}
export interface GrantFilters {
  live?: boolean;
  department?: string;
  resource?: string;
  limit?: number;
  offset?: number;
}
export interface GrantsPage extends Page<OrgGrant> {
  summary: GrantSummary | null;
}

// ---- requests ------------------------------------------------------------------------------------

export type OrgRequest = AccessRequest & OrgFields;
export interface RequestFilters {
  status?: "pending" | "approved" | "denied" | "decided" | "";
  department?: string;
  kind?: string;
  limit?: number;
  offset?: number;
}

// ---- org lookups ---------------------------------------------------------------------------------

export interface OrgDepartment {
  name: string;
  owner?: string;
  headcount: number;
  active: number;
  teams: number;
  usd: number;
  incidents_open: number;
  people_at_risk: number;
}
export interface OrgSummary {
  name: string;
  headcount: number;
  active: number;
  teams: number;
  departments_count: number;
  totals: { usd: number; incidents_open: number; people_at_risk: number; adherence: number | null; [k: string]: number | null };
  departments: OrgDepartment[];
}
export interface OrgTeam {
  name: string;
  department: string;
  headcount: number;
  incidents_open?: number;
  people_at_risk?: number;
}
export interface PersonHit {
  principal: string;
  name?: string | null;
  email?: string | null;
  team: string;
  department?: string | null;
  role?: string;
  status?: string;
  risk?: number;
  level?: string;
  open_incidents?: number;
}
export interface RiskOutlier {
  principal: string;
  name?: string | null;
  team: string;
  department: string;
  risk: number;
  level: string;
  status: string;
  open_incidents: number;
}

// ---- normalisers ---------------------------------------------------------------------------------

function page<T>(rows: T[], total: unknown, limit: number | undefined, offset = 0): Page<T> {
  const t = typeof total === "number" ? total : null;
  return { rows, total: t, hasMore: t !== null ? offset + rows.length < t : !!limit && rows.length >= limit };
}

/** Pulls the row array out of an envelope whose key the contract does not pin down. */
function rowsOf<T>(r: unknown, ...keys: string[]): T[] {
  if (Array.isArray(r)) return r as T[];
  if (r && typeof r === "object") for (const k of [...keys, "rows", "items"]) if (Array.isArray((r as Record<string, unknown>)[k])) return (r as Record<string, T[]>)[k];
  return [];
}

// ---- client --------------------------------------------------------------------------------------

export const ops = {
  incidents: async (f: IncidentFilters): Promise<IncidentsPage> => {
    const status = f.status === "active" ? "open,acknowledged" : f.status;
    const r = await get<{ incidents: OrgIncident[]; total?: number; scores: Record<string, number> }>(
      `${A}/incidents${qs({ sort: "severity", ...f, status })}`,
    );
    return { ...page(r.incidents ?? [], r.total, f.limit, f.offset), scores: r.scores ?? {} };
  },
  /** Just the count behind a filter (asks for one row). */
  incidentCount: async (f: IncidentFilters): Promise<number | null> => (await ops.incidents({ ...f, limit: 1, offset: 0 })).total,
  incidentSummary: (days = 30) => get<IncidentSummary>(`${A}/incidents/summary${qs({ days })}`),

  leases: (f: LeaseFilters = {}) => get<LeasesResponse>(`${A}/leases${qs({ ...f })}`),

  grants: async (f: GrantFilters): Promise<GrantsPage> => {
    const r = await get<unknown>(`${A}/grants${qs({ envelope: 1, ...f })}`);
    const rows = rowsOf<OrgGrant>(r, "grants");
    const o = (r && typeof r === "object" && !Array.isArray(r) ? r : {}) as { total?: number; summary?: GrantSummary };
    return { ...page(rows, o.total, f.limit, f.offset), summary: o.summary ?? null };
  },

  requests: async (f: RequestFilters): Promise<Page<OrgRequest>> => {
    const r = await get<unknown>(`${A}/requests${qs({ ...f })}`);
    const o = (r && typeof r === "object" && !Array.isArray(r) ? r : {}) as { total?: number };
    return page(rowsOf<OrgRequest>(r, "requests"), o.total, f.limit, f.offset);
  },

  activity: (p: { limit?: number; before?: number; source?: string; kind?: string; principal?: string; severity?: string; interesting?: boolean; department?: string }) =>
    get<(ActivityEvent & OrgFields)[]>(`${A}/activity${qs(p)}`),

  usage: (by: string, days = 30, filters: { department?: string; team?: string } = {}) =>
    get<Breakdown[]>(`${A}/usage${qs({ by, days, ...filters })}`),

  adherenceByDay: (days = 30) =>
    get<{ rows: { key: string; total: number; adherence: number | null }[]; overall: { adherence: number | null; total: number } | null }>(
      `${A}/adherence${qs({ by: "day", days })}`,
    ),

  org: (days = 30) => get<OrgSummary>(`${A}/org${qs({ days })}`),
  teams: (department?: string, limit = 100) =>
    get<{ total: number; rows: OrgTeam[] }>(`${A}/org/teams${qs({ department, limit, sort: "headcount" })}`),
  people: (p: { q?: string; department?: string; team?: string; status?: string; sort?: string; order?: "asc" | "desc"; limit?: number; offset?: number; days?: number }) =>
    get<{ total: number; rows: PersonHit[] }>(`${A}/people${qs(p)}`),
  riskOutliers: async (limit = 20, days = 7): Promise<RiskOutlier[]> =>
    (await get<{ risk?: RiskOutlier[] }>(`${A}/outliers${qs({ days, limit })}`)).risk ?? [],
};

/** "Ada Lovelace" when the directory has a name, else the principal id. */
export const displayName = (r: { principal?: string | null; name?: string | null }) => r.name || r.principal || "?";
