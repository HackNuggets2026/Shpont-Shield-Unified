import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { admin } from "../../api";
import { ops, type IncidentFilters } from "../../opsApi";
import { ActivityFeed } from "../../components/ActivityFeed";
import { AdminLog } from "../../components/AdminLog";
import { LevelPill, PersonStatusPill } from "../../components/pills";
import { Card, Empty, ErrorBox, Kpi, Loading, PageHeader, Pill, Q, Segmented, Select, cx } from "../../components/ui";
import { IconAlert } from "../../components/icons";
import { DepartmentSelect, OrgLine, Pager, SearchBox, TeamSelect, useDepartments, useUrlFilters } from "../../components/opsKit";
import { IncidentsTable } from "../../components/security/IncidentsTable";
import { OpenedClosedChart } from "../../components/security/OpenedClosedChart";
import { RiskMeter } from "../../components/security/RiskMeter";
import { RuleDeptHeatmap, type Cell } from "../../components/security/RuleDeptHeatmap";
import { countC } from "../../lib/compact";
import { detectionPolicy, isAuto, ruleLabel, RULES } from "../../lib/security";

type StatusFilter = "active" | "open" | "acknowledged" | "resolved" | "dismissed" | "all";
const STATUS_FILTERS: StatusFilter[] = ["active", "open", "acknowledged", "resolved", "dismissed", "all"];
const STATUS_LABEL: Record<StatusFilter, string> = {
  active: "Needs attention",
  open: "Open",
  acknowledged: "Acknowledged",
  resolved: "Resolved",
  dismissed: "Dismissed",
  all: "All",
};
const SEVERITIES = ["high", "medium", "low"] as const;
const PAGE = 25;
const KEYS = ["status", "severity", "rule", "department", "team", "q", "person"] as const;

/** Open incidents per severity: from the summary when the server gives it, else one count query each. */
function useOpenBySeverity(fromSummary: Record<string, number> | undefined) {
  const q = useQuery({
    queryKey: ["admin", "incidents", "open-by-severity"],
    queryFn: async () => {
      const n = await Promise.all(SEVERITIES.map((s) => ops.incidentCount({ status: "open", severity: s })));
      return Object.fromEntries(SEVERITIES.map((s, i) => [s, n[i] ?? 0])) as Record<string, number>;
    },
    enabled: fromSummary === undefined,
    refetchInterval: 10_000,
  });
  return fromSummary ?? q.data;
}

/** Rule × department counts: from the summary when the server gives it, else aggregated from the 30-day list. */
function useMatrix(fromSummary: Cell[] | undefined, ready: boolean) {
  const q = useQuery({
    queryKey: ["admin", "incidents", "matrix-30d"],
    queryFn: async () => {
      const r = await ops.incidents({ days: 30, limit: 5000 });
      const m = new Map<string, Cell>();
      for (const i of r.rows) {
        const d = i.department || "Unassigned";
        const k = `${i.rule}|${d}`;
        const c = m.get(k) ?? { rule: i.rule, department: d, open: 0, total: 0 };
        c.total++;
        if (i.status === "open" || i.status === "acknowledged") c.open++;
        m.set(k, c);
      }
      return [...m.values()];
    },
    enabled: ready && !fromSummary,
    refetchInterval: 15_000,
  });
  return { cells: fromSummary ?? q.data, q };
}

export function Security() {
  const f = useUrlFilters(KEYS, { status: "active" });
  const v = f.values;
  const status = (STATUS_FILTERS.includes(v.status as StatusFilter) ? v.status : "active") as StatusFilter;
  const tableRef = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<"open" | "total">("open");

  const summary = useQuery({ queryKey: ["admin", "incidents", "summary", 30], queryFn: () => ops.incidentSummary(30), refetchInterval: 10_000 });
  const openBySev = useOpenBySeverity(summary.data?.by_severity?.open);
  const matrix = useMatrix(summary.data?.by_rule_department, summary.isSuccess || summary.isError);
  const outliers = useQuery({
    queryKey: ["admin", "outliers", "risk", 20],
    queryFn: async () => (await ops.riskOutliers(20)).filter((p) => p.risk >= 0.5 || p.status !== "active"),
    refetchInterval: 10_000,
  });
  const { q: org } = useDepartments();
  const acts = useQuery({ queryKey: ["admin", "actions", "all"], queryFn: () => admin.actions(), refetchInterval: 10_000 });
  const pol = useQuery({ queryKey: ["admin", "policy", "detections"], queryFn: detectionPolicy, staleTime: 60_000 });

  const filters: IncidentFilters = {
    status: status === "all" ? "" : status,
    severity: v.severity,
    rule: v.rule,
    department: v.department,
    team: v.team,
    principal: v.person,
    q: v.q,
  };
  const list = useQuery({
    queryKey: ["admin", "incidents", "page", filters, f.page],
    queryFn: () => ops.incidents({ ...filters, limit: PAGE, offset: f.page * PAGE }),
    placeholderData: keepPreviousData,
    refetchInterval: 5_000,
  });

  const s = summary.data;
  const statusCounts = useMemo(() => {
    if (!s) return null;
    const sum = (k: "open" | "acknowledged" | "resolved" | "dismissed" | "total") => s.by_rule.reduce((a, r) => a + (r[k] ?? 0), 0);
    const c = { open: sum("open"), acknowledged: sum("acknowledged"), resolved: sum("resolved"), dismissed: sum("dismissed"), all: sum("total") };
    return { ...c, active: c.open + c.acknowledged } as Record<StatusFilter, number>;
  }, [s]);
  const openTotal = openBySev ? SEVERITIES.reduce((a, k) => a + (openBySev[k] ?? 0), 0) : statusCounts?.open;
  const atRisk = s ? s.by_department.reduce((a, d) => a + (d.people_at_risk ?? 0), 0) : (org.data?.totals.people_at_risk ?? undefined);
  const rules = useMemo(() => {
    const known = new Set([...(s?.by_rule.map((r) => r.rule) ?? []), ...Object.keys(RULES)]);
    return [...known].sort((a, b) => ruleLabel(a).localeCompare(ruleLabel(b)));
  }, [s]);
  const th = pol.data?.response ?? null;

  const toTable = () => requestAnimationFrame(() => tableRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  const pickCell = (rule: string, department: string) => {
    f.set({ rule, department, team: "", severity: "", q: "", person: "", status: mode === "open" ? "active" : "all" });
    toTable();
  };
  const pickDept = (department: string) => {
    f.set({ department, team: "" });
    toTable();
  };
  const unfiltered = status === "active" && !v.severity && !v.rule && !v.department && !v.team && !v.q && !v.person;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Security"
        subtitle="AI usage that looks like an attack, across the whole organization: by rule and department first, then the incidents that need a human."
      />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Kpi
          label="Open incidents"
          icon={<IconAlert />}
          tone={openBySev?.high ? "bad" : openTotal ? "warn" : undefined}
          value={openTotal === undefined ? "…" : countC(openTotal)}
          sub={
            <span className="flex flex-wrap items-center gap-1">
              {SEVERITIES.map((sv) => (
                <button
                  key={sv}
                  type="button"
                  onClick={() => {
                    f.set({ status: "open", severity: sv, rule: "", department: "", team: "", q: "", person: "" });
                    toTable();
                  }}
                  className="hover:opacity-80"
                >
                  <Pill tone={openBySev?.[sv] ? (sv === "high" ? "bad" : sv === "medium" ? "serious" : "warn") : "neutral"}>
                    {openBySev ? countC(openBySev[sv] ?? 0) : "…"} {sv}
                  </Pill>
                </button>
              ))}
              {statusCounts && statusCounts.acknowledged > 0 && <span className="text-[11px]">+{countC(statusCounts.acknowledged)} acknowledged</span>}
            </span>
          }
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card
          className="xl:col-span-2"
          title="Where incidents happen"
          subtitle="Rule × department. Click a cell to list its incidents below."
          actions={
            <Segmented
              value={mode}
              onChange={setMode}
              options={[
                { value: "open", label: "Needs attention" },
                { value: "total", label: "Last 30 days" },
              ]}
            />
          }
        >
          {matrix.cells ? (
            <RuleDeptHeatmap
              cells={matrix.cells}
              mode={mode}
              onPick={pickCell}
              selected={v.rule && v.department ? { rule: v.rule, department: v.department } : undefined}
            />
          ) : matrix.q.isError ? (
            <ErrorBox error={matrix.q.error} retry={() => matrix.q.refetch()} />
          ) : (
            <Loading rows={6} />
          )}
        </Card>

        <div className="min-w-0 space-y-4">
          <Card title="Opened vs. closed" subtitle="Incidents per day, last 30 days">
            <Q q={summary} rows={5}>
              {(d) => <OpenedClosedChart trend={d.trend} height={170} />}
            </Q>
          </Card>
          <Card title="By department" subtitle="Open incidents and people past the alert level. Click to filter." flush>
            <Q q={summary} rows={4}>
              {(d) =>
                d.by_department.length === 0 ? (
                  <Empty title="No incidents" />
                ) : (
                  <ul className="divide-y divide-line/60">
                    {[...d.by_department]
                      .sort((a, b) => b.open - a.open || b.total - a.total)
                      .map((r) => (
                        <li key={r.department}>
                          <button
                            type="button"
                            onClick={() => pickDept(r.department)}
                            className={cx(
                              "flex w-full items-center justify-between gap-3 px-4 py-2 text-left text-xs hover:bg-raised/60",
                              v.department === r.department && "bg-accent/[0.06]",
                            )}
                          >
                            <span className="min-w-0 truncate font-medium text-ink">{r.department}</span>
                            <span className="tnum flex shrink-0 items-center gap-3 text-muted">
                              <span className={r.open ? "font-semibold text-bad" : ""}>{countC(r.open)} open</span>
                              <span>{countC(r.people_at_risk)} at risk</span>
                              <span className="hidden sm:inline">{countC(r.total)} in 30d</span>
                            </span>
                          </button>
                        </li>
                      ))}
                  </ul>
                )
              }
            </Q>
          </Card>
        </div>
      </div>

      <div ref={tableRef} className="min-w-0 scroll-mt-4">
        <Card
          flush
          title={
            <span>
              Incidents{" "}
              <span className="font-normal text-muted">
                · {list.data ? (list.data.total !== null ? countC(list.data.total) : `${list.data.rows.length}${list.data.hasMore ? "+" : ""}`) : "…"}
              </span>
            </span>
          }
          subtitle="High severity first, then open before acknowledged, then newest. Click one for its evidence."
        >
          <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2">
            <Select
              label="Severity"
              value={v.severity}
              onChange={(x) => f.set({ severity: x })}
              options={[{ value: "", label: "Any severity" }, ...SEVERITIES.map((x) => ({ value: x, label: x }))]}
            />
            <Select
              label="Rule"
              value={v.rule}
              onChange={(x) => f.set({ rule: x })}
              options={[{ value: "", label: "Any rule" }, ...rules.map((r) => ({ value: r, label: ruleLabel(r) }))]}
            />
            <DepartmentSelect value={v.department} onChange={(x) => f.set({ department: x, team: "" })} />
            <TeamSelect department={v.department} value={v.team} onChange={(x) => f.set({ team: x })} />
            <SearchBox value={v.q} onChange={(x) => f.set({ q: x })} placeholder="Person: name, email, id" />
            {v.person && (
              <Pill tone="accent">
                person {v.person}{" "}
                <button type="button" className="ml-1" onClick={() => f.set({ person: "" })} aria-label="Clear person">
                  ×
                </button>
              </Pill>
            )}
            {f.dirty && (
              <button type="button" onClick={f.reset} className="text-xs font-medium text-accent hover:underline">
                Reset
              </button>
            )}
          </div>
          <div className="overflow-x-auto border-b border-line px-4 py-2">
            <Segmented<StatusFilter>
              value={status}
              onChange={(x) => f.set({ status: x })}
              options={STATUS_FILTERS.map((x) => ({
                value: x,
                label: (
                  <span className="whitespace-nowrap">
                    {STATUS_LABEL[x]}{" "}
                    {statusCounts && !v.severity && !v.rule && !v.department && !v.team && !v.q && !v.person && x !== "all" && (
                      <span className="tnum text-muted" title="last 30 days">
                        {countC(statusCounts[x])}
                      </span>
                    )}
                  </span>
                ),
              }))}
            />
          </div>
          <Q q={list} rows={8}>
            {(d) => (
              <>
                <IncidentsTable
                  incidents={d.rows}
                  empty={unfiltered ? "Nothing needs attention" : "No incidents match the filters"}
                  hint={unfiltered ? "Every incident is resolved or dismissed." : undefined}
                />
                <Pager
                  page={f.page}
                  size={PAGE}
                  shown={d.rows.length}
                  total={d.total}
                  hasMore={d.hasMore}
                  onPage={f.setPage}
                  fetching={list.isPlaceholderData}
                  className="border-t border-line"
                />
              </>
            )}
          </Q>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title="People at risk"
          subtitle={
            atRisk !== undefined && atRisk > 20
              ? `Top 20 of ${countC(atRisk)}, highest risk score first`
              : pol.data
                ? `Decaying sum of open incident weights; halves every ${Math.round(pol.data.half_life_minutes)} min`
                : "Highest risk score first"
          }
          actions={
            <Link to="/console/people?sort=risk" className="text-xs font-medium text-accent hover:underline">
              All people →
            </Link>
          }
          flush
        >
          {outliers.isPending ? (
            <div className="p-4">
              <Loading rows={5} />
            </div>
          ) : outliers.isError ? (
            <div className="p-4">
              <ErrorBox error={outliers.error} retry={() => outliers.refetch()} />
            </div>
          ) : outliers.data.length === 0 ? (
            <Empty title="Nobody carries risk right now" hint="Scores rise with incidents and decay on their own." />
          ) : (
            <ul className="max-h-[480px] divide-y divide-line/60 overflow-y-auto">
              {outliers.data.map((p) => (
                <li key={p.principal}>
                  <Link
                    to={`/console/people/${encodeURIComponent(p.principal)}`}
                    className={cx("flex items-center justify-between gap-3 px-4 py-2.5 hover:bg-raised/60", p.level === "quarantine" && "bg-bad/[0.04]")}
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="font-medium text-ink">{p.name || p.principal}</span>
                        {p.level && p.level !== "none" && <LevelPill level={p.level} />}
                        {p.status && p.status !== "active" && <PersonStatusPill status={p.status} />}
                      </div>
                      <OrgLine team={p.team} department={p.department} className="mt-0.5" />
                      <div className="text-[11px] text-muted">
                        {p.open_incidents} open incident{p.open_incidents === 1 ? "" : "s"}
                      </div>
                    </div>
                    <RiskMeter score={p.risk} thresholds={th} compact />
                  </Link>
                </li>
              ))}
            </ul>
          )}
          {th && (
            <div className="flex flex-wrap gap-x-3 gap-y-1 border-t border-line px-4 py-2 text-[11px] text-muted">
              <span>
                alert ≥ <b className="text-ink2">{th.alert}</b>
              </span>
              <span>
                tighten ≥ <b className="text-ink2">{th.tighten}</b> (budget to {Math.round(th.tighten_budget_scale * 100)}%)
              </span>
              <span>
                quarantine ≥ <b className="text-ink2">{th.quarantine}</b>
              </span>
              {!th.auto && <span className="text-warn">automatic responses off</span>}
            </div>
          )}
        </Card>

        <Card title="What the system did on its own" subtitle="Latest automatic responses" flush>
          <Q q={acts} rows={3}>
            {(d) => (
              <div className="max-h-[520px] overflow-y-auto">
                <AdminLog actions={d.filter(isAuto).slice(0, 15)} empty="No automatic responses yet" />
              </div>
            )}
          </Q>
        </Card>
      </div>

      <Card
        title="Security signal"
        subtitle="Live: blocks, redactions, incidents, grants, zombie flags and Claude Code rejections. Allowed checks are left out."
        flush
      >
        <ActivityFeed
          load={(p) => ops.activity({ ...p, interesting: true })}
          queryKey={["admin", "activity", "security", "interesting"]}
          linkPeople
          maxH="420px"
          emptyHint="Blocked attacks, exfiltration attempts and the incidents they raise show up here."
        />
      </Card>
    </div>
  );
}
