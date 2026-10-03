// Everything the workflow-first console needs, joined per workflow from existing endpoints:
// the menu (limits, measured cost per run), usage by workflow / department / resource / team,
// the 30-day spend series, adherence, and live leases.
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { admin, get, type Breakdown, type Incident, type Lease, type Workflow } from "../api";
import { org } from "../orgApi";
import { workflowColors } from "./workflows";

export const UNATTRIBUTED = ["(none)", "unlabeled"];

export interface WfRow {
  name: string;
  wf: Workflow | null;
  color: string;
  usd: number;
  usdPrev: number;
  /** Spend over the last 14 full days vs the 14 before (from the daily series; today is partial and left out). */
  trend: { recent: number; earlier: number };
  requests: number;
  runs: number;
  p50: number | null;
  p90: number | null;
  mean: number | null;
  spark: number[];
  adherence: number | null;
  checks: number;
  blocked: number;
  redacted: number;
  warned: number;
  depts: { department: string; usd: number }[];
  resources: { resource: string; usd: number; minutes: number }[];
  running: Lease[];
}

const key = (r: Breakdown, dim: string) => String(r[dim] ?? "(none)");

export function useMenu() {
  return useQuery({ queryKey: ["admin", "menu"], queryFn: admin.menu, refetchInterval: 30_000 });
}

export function useWorkflowBoard() {
  const menu = useMenu();
  const u30 = useQuery({ queryKey: ["admin", "usage", "workflow", 30], queryFn: () => org.usage("workflow", 30), refetchInterval: 60_000 });
  const u60 = useQuery({ queryKey: ["admin", "usage", "workflow", 60], queryFn: () => org.usage("workflow", 60), refetchInterval: 120_000 });
  const ts = useQuery({ queryKey: ["admin", "timeseries", "workflow", 30], queryFn: () => admin.timeseries("workflow", 30), refetchInterval: 60_000 });
  const adh = useQuery({ queryKey: ["admin", "adherence", "workflow", 30], queryFn: () => admin.adherence("workflow", 30), refetchInterval: 60_000 });
  const byDept = useQuery({ queryKey: ["admin", "usage", "workflow,department", 30], queryFn: () => org.usage("workflow,department", 30), retry: 1, refetchInterval: 60_000 });
  const byRes = useQuery({ queryKey: ["admin", "usage", "workflow,resource", 30], queryFn: () => org.usage("workflow,resource", 30), retry: 1, refetchInterval: 60_000 });
  const leases = useQuery({ queryKey: ["admin", "leases"], queryFn: admin.leases, refetchInterval: 10_000 });

  const rows = useMemo<WfRow[]>(() => {
    const wfs = menu.data?.workflows ?? [];
    const colors = workflowColors(wfs.map((w) => w.name));
    const names = new Set<string>(wfs.map((w) => w.name));
    for (const r of u30.data ?? []) names.add(key(r, "workflow"));
    const usd30: Record<string, Breakdown> = Object.fromEntries((u30.data ?? []).map((r) => [key(r, "workflow"), r]));
    const usd60: Record<string, number> = Object.fromEntries((u60.data ?? []).map((r) => [key(r, "workflow"), r.usd]));
    const adhRows = Object.fromEntries((adh.data?.rows ?? []).map((r) => [r.key, r]));
    return [...names].map((name) => {
      const wf = wfs.find((w) => w.name === name) ?? null;
      const a = adhRows[name];
      const cur = usd30[name]?.usd ?? 0;
      const sp = ts.data?.series[name] ?? [];
      const full = sp.slice(0, -1);
      const sum = (a: number[]) => a.reduce((x, y) => x + y, 0);
      return {
        name,
        wf,
        color: colors[name] ?? "var(--s-other)",
        usd: cur,
        usdPrev: Math.max((usd60[name] ?? 0) - cur, 0),
        trend: { recent: sum(full.slice(-14)), earlier: sum(full.slice(-28, -14)) },
        requests: usd30[name]?.requests ?? 0,
        runs: wf?.measured.runs ?? 0,
        p50: wf?.measured.usd_p50 ?? null,
        p90: wf?.measured.usd_p90 ?? null,
        mean: wf?.measured.usd_mean ?? null,
        spark: ts.data?.series[name] ?? [],
        adherence: a?.adherence ?? null,
        checks: a?.total ?? 0,
        blocked: a?.block ?? 0,
        redacted: a?.redact ?? 0,
        warned: a?.warn ?? 0,
        depts: (byDept.data ?? [])
          .filter((r) => key(r, "workflow") === name && r.usd > 0)
          .map((r) => ({ department: key(r, "department"), usd: r.usd }))
          .sort((x, y) => y.usd - x.usd),
        resources: (byRes.data ?? [])
          .filter((r) => key(r, "workflow") === name && (r.usd > 0.5 || r.minutes > 0))
          .map((r) => ({ resource: key(r, "resource"), usd: r.usd, minutes: r.minutes }))
          .sort((x, y) => y.usd - x.usd),
        running: (leases.data?.open ?? []).filter((l) => (l.workflow ?? "(none)") === name),
      };
    });
  }, [menu.data, u30.data, u60.data, ts.data, adh.data, byDept.data, byRes.data, leases.data]);

  const menuRows = rows.filter((r) => r.wf).sort((a, b) => b.usd - a.usd);
  const offMenu = rows.filter((r) => !r.wf && r.usd > 0);
  return {
    menu,
    rows,
    menuRows,
    offMenu,
    leases,
    adherence: adh,
    byDept,
    byRes,
    isPending: menu.isPending || u30.isPending,
    error: menu.error ?? u30.error,
    refetch: () => {
      void menu.refetch();
      void u30.refetch();
    },
  };
}

/** One workflow's daily spend split by department: one timeseries call per department (filters exist, a workflow filter does not). */
export function useWorkflowByDeptSeries(workflow: string, departments: string[]) {
  return useQuery({
    queryKey: ["admin", "wf-dept-series", workflow, departments.join("|")],
    enabled: departments.length > 0,
    staleTime: 60_000,
    queryFn: async () => {
      const all = await Promise.all(departments.map((d) => org.timeseries("workflow", 30, { department: d }).then((ts) => [d, ts] as const)));
      const days = all[0]?.[1].days ?? [];
      const series: Record<string, number[]> = {};
      for (const [d, ts] of all) {
        const s = ts.series[workflow];
        if (s && s.some((v) => v > 0)) series[d] = s;
      }
      const ordered = Object.fromEntries(Object.entries(series).sort((a, b) => b[1].reduce((x, y) => x + y, 0) - a[1].reduce((x, y) => x + y, 0)));
      const totals = days.map((_, i) => Object.values(ordered).reduce((s, v) => s + (v[i] ?? 0), 0));
      return { days, series: ordered, totals };
    },
  });
}

export function useWorkflowTeams() {
  return useQuery({ queryKey: ["admin", "usage", "workflow,team", 30], queryFn: () => org.usage("workflow,team", 30), staleTime: 60_000 });
}

// ---- incidents, tagged with the workflow they happened in ----------------------------------------

export interface WfTag {
  workflow: string | null;
  /** Inferred from the person's usual work, not read off the evidence. */
  likely: boolean;
}

const CC_RULES = new Set(["permission_bypass", "unapproved_mcp_server", "rejected_edit_storm"]);

async function pool<T, R>(items: T[], n: number, fn: (t: T) => Promise<R>): Promise<R[]> {
  const out: R[] = new Array(items.length);
  let i = 0;
  await Promise.all(
    Array.from({ length: Math.min(n, items.length) }, async () => {
      while (i < items.length) {
        const k = i++;
        out[k] = await fn(items[k]);
      }
    }),
  );
  return out;
}

/**
 * Incidents carry no workflow. The evidence does: we read it for every incident that still needs attention
 * or is recent, and fall back to the person's own workflow mix (narrowed by what the rule implies: a zombie
 * VM points at workflows that lease VMs, a Claude Code rule at workflows run in Claude Code). Fallbacks are
 * marked `likely`.
 */
export function useIncidentTags() {
  return useQuery({
    queryKey: ["admin", "incident-tags"],
    staleTime: 60_000,
    refetchInterval: 60_000,
    queryFn: async (): Promise<Record<string, WfTag>> => {
      const [inc, menu, byRes] = await Promise.all([admin.incidents(), admin.menu(), org.usage("workflow,resource", 30)]);
      const now = Date.now() / 1000;
      const onMenu = new Set(menu.workflows.map((w) => w.name));
      const usesResource = (res: string) => menu.workflows.filter((w) => res in w.resources).map((w) => w.name);
      const ccWorkflows = new Set(byRes.filter((r) => r.resource === "claude_code" && r.usd > 0).map((r) => String(r.workflow)));
      const tags: Record<string, WfTag> = {};

      const needEvidence = inc.incidents.filter((i) => i.status === "open" || i.status === "acknowledged" || now - i.ts < 7 * 86400).slice(0, 90);
      await pool(needEvidence, 6, async (i: Incident) => {
        try {
          const d = await admin.incident(i.id);
          const counts: Record<string, number> = {};
          for (const e of d.timeline) if (e.workflow && onMenu.has(e.workflow)) counts[e.workflow] = (counts[e.workflow] ?? 0) + (e.evidence ? 3 : 1);
          const best = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
          if (best) tags[i.id] = { workflow: best[0], likely: false };
        } catch {
          /* fall back below */
        }
      });

      const rest = inc.incidents.filter((i) => !tags[i.id]);
      const people = [...new Set(rest.map((i) => i.principal))];
      const mixes: Record<string, Breakdown[]> = {};
      await pool(people, 8, async (pid) => {
        mixes[pid] = await get<Breakdown[]>(`/api/admin/usage?by=workflow&days=30&principal=${encodeURIComponent(pid)}`).catch(() => []);
      });
      for (const i of rest) {
        if (i.rule === "unlabeled_resource") {
          tags[i.id] = { workflow: null, likely: false };
          continue;
        }
        let allowed: Set<string> | null = null;
        if (i.rule === "zombie_resource") {
          const res = /^(\w+)\s/.exec(i.detail)?.[1] ?? "";
          const c = usesResource(res);
          if (c.length) allowed = new Set(c);
        } else if (CC_RULES.has(i.rule)) allowed = ccWorkflows;
        const mix = (mixes[i.principal] ?? []).filter((r) => onMenu.has(String(r.workflow)) && (!allowed || allowed.has(String(r.workflow)))).sort((a, b) => b.usd - a.usd);
        const pick = mix[0] ? String(mix[0].workflow) : allowed && allowed.size ? [...allowed][0] : null;
        tags[i.id] = { workflow: pick, likely: true };
      }
      return tags;
    },
  });
}
