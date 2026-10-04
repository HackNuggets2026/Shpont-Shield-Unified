import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { admin } from "../../api";
import { org, orgPath, type CostOutlier, type OrgIncident } from "../../orgApi";
import { Sparkline, StackedChart } from "../../components/charts";
import { Delta, UnitTable, useDeptColors, useOrg } from "../../components/org";
import { SeverityPill } from "../../components/pills";
import { Card, Empty, ErrorBox, Meter, PageHeader, Q, Segmented, Skeleton, TONE_COLOR, Wash, cx } from "../../components/ui";
import { useWorkflowColors, wfLabel } from "../../lib/workflows";
import { ago, count, money, share, times, unitMoney } from "../../lib/format";

export const WINDOWS: { value: string; label: string }[] = [
  { value: "7", label: "7d" },
  { value: "30", label: "30d" },
  { value: "90", label: "90d" },
];

const DAYS = 30;

function daysInMonth(d = new Date()) {
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate();
}

/** Claude Code spend per commit, from the server-side value rollup (by department, summed). */
export function useCcValue(days: number, f: { department?: string; team?: string } = {}) {
  return useQuery({
    queryKey: ["admin", "value", "department", days, f],
    queryFn: () => org.value("department", days, f),
    staleTime: 60_000,
    refetchInterval: 60_000,
    select: (d) => {
      const s = d.rows.reduce(
        (a, r) => ({ cc: a.cc + r.claude_code_usd, usd: a.usd + r.usd, commits: a.commits + r.commits, prs: a.prs + r.pull_requests }),
        { cc: 0, usd: 0, commits: 0, prs: 0 },
      );
      return { ...s, perCommit: s.commits ? s.cc / s.commits : null, rows: d.rows };
    },
  });
}

/** A big number with one line of context: spend against budget and open incidents, each linking to where it is acted on. */
function Tile({
  label,
  value,
  children,
  to,
  tone,
  wash = "accent",
  aside,
}: {
  label: string;
  value: ReactNode;
  children?: ReactNode;
  to?: string;
  tone?: "bad" | "warn";
  wash?: "accent" | "cc" | "good" | "warn" | "bad";
  aside?: ReactNode;
}) {
  const body = (
    <div className={cx("soft-card relative h-full overflow-hidden rounded-2xl p-3.5 sm:p-5", to && "transition hover:-translate-y-0.5 hover:shadow-lg")}>
      <Wash color={TONE_COLOR[wash]} height="7rem" opacity={0.09} />
      <div className="relative text-xs font-medium text-muted">{label}</div>
      <div className="relative mt-2 flex items-end justify-between gap-3">
        <div className={cx("tnum text-2xl font-semibold tracking-tight sm:text-3xl", tone === "bad" ? "text-bad" : tone === "warn" ? "text-warn" : "text-ink")}>{value}</div>
        {aside && <div className="hidden w-[42%] max-w-[140px] sm:block">{aside}</div>}
      </div>
      {children && <div className="relative mt-2 space-y-1.5 text-xs text-muted">{children}</div>}
    </div>
  );
  return to ? (
    <Link to={to} className="block rounded-2xl focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50">
      {body}
    </Link>
  ) : (
    body
  );
}

function Tiles() {
  const ov = useQuery({ queryKey: ["admin", "overview"], queryFn: admin.overview, refetchInterval: 10_000 });
  const o = useOrg(DAYS);
  const ts = useQuery({ queryKey: ["admin", "timeseries", "workflow", DAYS], queryFn: () => org.timeseries("workflow", DAYS), refetchInterval: 60_000 });
  if (o.isError) return <ErrorBox error={o.error} retry={() => o.refetch()} />;
  if (!o.data || !ov.data)
    return (
      <div className="grid gap-3 sm:grid-cols-2 sm:gap-4">
        {Array.from({ length: 2 }, (_, i) => (
          <Skeleton key={i} className="h-[136px] rounded-2xl" />
        ))}
      </div>
    );
  const t = o.data.totals;
  const v = ov.data;
  const budget = v.global_usd_per_day ? v.global_usd_per_day * daysInMonth() : null;
  const over = budget ? v.spend.month_forecast > budget : false;
  return (
    <div className="grid gap-3 sm:grid-cols-2 sm:gap-4">
      <Tile
        label="AI spend, month to date"
        value={money(v.spend.month_to_date)}
        tone={over ? "bad" : undefined}
        wash={over ? "bad" : "accent"}
        to="/console/controls"
        aside={ts.data && <Sparkline values={ts.data.totals.slice(0, -1)} color="rgb(var(--accent))" height={36} min={0} />}
      >
        {budget ? <Meter value={v.spend.month_forecast} max={budget} /> : null}
        <div>
          Forecast <span className={over ? "font-semibold text-bad" : "font-medium text-ink2"}>{money(v.spend.month_forecast)}</span>
          {budget ? ` of ${money(budget)} budget` : ""}
        </div>
        <div>
          Last {DAYS} days {money(t.usd)} <Delta cur={t.usd} prev={t.usd_prev} />
        </div>
      </Tile>
      <Tile
        label="Open incidents"
        value={count(t.incidents_open)}
        tone={t.incidents_open ? "bad" : undefined}
        wash={t.incidents_open ? "bad" : "good"}
        to="/console/security"
      >
        <div className={t.people_at_risk ? "font-medium text-warn" : undefined}>
          {count(t.people_at_risk)} {t.people_at_risk === 1 ? "person" : "people"} past the alert level
        </div>
      </Tile>
    </div>
  );
}

function SpendCard() {
  const [by, setBy] = useState<"department" | "workflow">("workflow");
  const deptColors = useDeptColors();
  const wfColors = useWorkflowColors();
  const ts = useQuery({ queryKey: ["admin", "timeseries", by, DAYS], queryFn: () => org.timeseries(by, DAYS), refetchInterval: 60_000 });
  const total = ts.data?.totals.reduce((a, b) => a + b, 0);
  return (
    <Card
      title={`Spend by ${by}, last ${DAYS} days`}
      subtitle={total !== undefined ? `${money(total)} in total` : undefined}
      actions={
        <Segmented
          value={by}
          onChange={setBy}
          options={[
            { value: "workflow", label: "By workflow" },
            { value: "department", label: "By department" },
          ]}
        />
      }
    >
      <Q q={ts} rows={8}>
        {(d) => (
          <StackedChart
            ts={d}
            kind="bar"
            compact
            height={280}
            colors={by === "department" ? deptColors : wfColors}
            labelOf={by === "workflow" ? wfLabel : undefined}
          />
        )}
      </Q>
    </Card>
  );
}

const SEV: Record<string, number> = { high: 3, medium: 2, low: 1, info: 0 };

type Item = { kind: "incident"; i: OrgIncident } | { kind: "cost"; c: CostOutlier };

/** Five things a person should look at: the worst open incidents, then the biggest cost outliers. */
function NeedsAttention() {
  const inc = useQuery({ queryKey: ["admin", "incidents", "open", "top"], queryFn: () => org.incidents({ status: "open", limit: 50 }), refetchInterval: 30_000 });
  const out = useQuery({ queryKey: ["admin", "outliers", 7, 5], queryFn: () => org.outliers({ days: 7, limit: 5 }), refetchInterval: 60_000 });
  const items = useMemo<Item[]>(() => {
    const incidents = [...(inc.data?.incidents ?? [])]
      .filter((i) => i.status === "open")
      .sort((a, b) => (SEV[b.severity] ?? 0) - (SEV[a.severity] ?? 0) || b.ts - a.ts)
      .slice(0, 3)
      .map((i) => ({ kind: "incident" as const, i }));
    const cost = (out.data?.cost ?? []).map((c) => ({ kind: "cost" as const, c }));
    return [...incidents, ...cost].slice(0, 5);
  }, [inc.data, out.data]);
  const openTotal = inc.data?.total ?? inc.data?.incidents.filter((i) => i.status === "open").length;
  return (
    <Card title="Needs attention" subtitle="Worst open incidents, then biggest cost outliers (7 days)" flush className="h-full">
      {inc.isPending && out.isPending ? (
        <div className="p-4">
          <Skeleton className="h-48" />
        </div>
      ) : inc.isError && out.isError ? (
        <div className="p-4">
          <ErrorBox error={inc.error} retry={() => inc.refetch()} />
        </div>
      ) : items.length === 0 ? (
        <Empty title="Nothing needs attention" hint="No open incidents and nobody far above their team's spend." />
      ) : (
        <ul className="divide-y divide-line/60">
          {items.map((it) =>
            it.kind === "incident" ? (
              <li key={it.i.id}>
                <Link
                  to={`/console/incidents/${encodeURIComponent(it.i.id)}`}
                  className={cx(
                    "flex items-start gap-3 border-l-[3px] px-4 py-3 transition-colors",
                    it.i.severity === "high" ? "border-bad bg-bad/[0.06] hover:bg-bad/10" : it.i.severity === "medium" ? "border-serious/70 hover:bg-raised/60" : "border-warn/60 hover:bg-raised/60",
                  )}
                >
                  <SeverityPill severity={it.i.severity} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-ink">{it.i.rule.replace(/_/g, " ")}</div>
                    <div className="truncate text-xs text-muted">
                      {ago(it.i.ts)} · {it.i.name || it.i.principal}
                      {it.i.team ? ` · ${it.i.team}` : it.i.department ? ` · ${it.i.department}` : ""}
                    </div>
                  </div>
                </Link>
              </li>
            ) : (
              <li key={`c:${it.c.principal}`}>
                <Link to={orgPath.person(it.c.principal)} className="flex items-start gap-3 border-l-[3px] border-transparent px-4 py-3 hover:bg-raised/60">
                  <span className="tnum inline-flex shrink-0 items-center rounded-full bg-serious/10 px-2 py-0.5 text-[11px] font-medium text-serious ring-1 ring-inset ring-serious/30">
                    {times(it.c.ratio)} cost
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-ink">
                      {it.c.name || it.c.principal} <span className="tnum font-normal text-ink2">{money(it.c.usd)}</span>
                    </div>
                    <div className="truncate text-xs text-muted">
                      {it.c.team} · {it.c.department} · team median {money(it.c.team_median)}
                    </div>
                  </div>
                </Link>
              </li>
            ),
          )}
        </ul>
      )}
      <div className="flex flex-wrap justify-between gap-2 border-t border-line/60 px-5 py-3 text-xs">
        <Link to="/console/security" className="font-medium text-accent hover:underline">
          {openTotal ? `All ${count(openTotal)} open incidents` : "Security"} →
        </Link>
        <Link to={`${orgPath.root}#outliers`} className="font-medium text-accent hover:underline">
          All outliers →
        </Link>
      </div>
    </Card>
  );
}

/** One quiet line for the numbers that live on other pages. */
function SecondaryLine() {
  const ov = useQuery({ queryKey: ["admin", "overview"], queryFn: admin.overview, refetchInterval: 10_000 });
  const o = useOrg(DAYS);
  const cc = useCcValue(DAYS);
  if (!o.data || !ov.data) return null;
  const t = o.data.totals;
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted">
      <Link to="/console/workflows" className="hover:text-ink">
        Claude Code: <span className="tnum text-ink2">{share(t.claude_code_users / Math.max(o.data.active, 1))}</span> of active people ·{" "}
        <span className="tnum text-ink2">{money(t.claude_code_usd)}</span>
        {cc.data?.perCommit != null && (
          <>
            {" "}
            · <span className="tnum text-ink2">{unitMoney(cc.data.perCommit)}</span>/commit
          </>
        )}
      </Link>
      <Link to="/console/resources" className="hover:text-ink">
        <span className="tnum text-ink2">{count(ov.data.leases_open)}</span> running resources
        {ov.data.zombies > 0 && <span className="text-warn"> · {ov.data.zombies} zombie</span>}
      </Link>
      <Link to="/console/requests" className="hover:text-ink">
        <span className="tnum text-ink2">{count(ov.data.requests_pending)}</span> requests pending
      </Link>
      <Link to="/console/activity" className="hover:text-ink">
        Live activity →
      </Link>
    </div>
  );
}

export function Overview() {
  const o = useOrg(DAYS);
  const colors = useDeptColors();
  const d = o.data;
  return (
    <div className="space-y-6">
      <PageHeader
        title={d?.name ?? "Overview"}
        subtitle={
          d ? (
            <span className="tnum">
              {count(d.headcount)} people · {count(d.active)} active · {count(d.teams)} teams · {count(d.departments_count)} departments · last {DAYS} days
            </span>
          ) : (
            "AI usage, cost and policy across the company"
          )
        }
      />
      <Tiles />
      <div className="grid gap-4 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <SpendCard />
        </div>
        <NeedsAttention />
      </div>
      <Card
        title="Departments"
        subtitle="Open one for its teams, workflows and outliers"
        actions={
          <Link to={orgPath.root} className="text-xs font-medium text-accent hover:underline">
            All teams →
          </Link>
        }
        flush
      >
        <Q q={o} rows={6}>
          {(x) => <UnitTable rows={x.departments} kind="department" colors={colors} compact empty="No departments yet" />}
        </Q>
      </Card>
      <SecondaryLine />
    </div>
  );
}
