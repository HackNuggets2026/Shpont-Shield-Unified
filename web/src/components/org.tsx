// Building blocks for the org-scale console: department colors, deltas, sortable unit tables, pagers,
// outlier lists, the team treemap and the global people/team search.
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ResponsiveContainer, Tooltip, Treemap } from "recharts";
import { org, orgPath, type CostOutlier, type GrowthOutlier, type RiskOutlier, type UnitRow } from "../orgApi";
import { count, delta, money, pctAuto, share, times, unitMoney, type DeltaDir } from "../lib/format";
import { LevelPill } from "./pills";
import { IconChevronRight, IconSearch } from "./icons";
import { Button, Empty, Segmented, TableWrap, cx } from "./ui";

// ---- org data hooks -------------------------------------------------------------------------

export function useOrg(days = 30) {
  return useQuery({ queryKey: ["admin", "org", days], queryFn: () => org.org(days), refetchInterval: 30_000, placeholderData: keepPreviousData });
}

// ---- department colors ----------------------------------------------------------------------

const SLOTS = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--s6)", "var(--s7)"];

/** One fixed hue per department (alphabetical order), so a department keeps its color on every chart. */
export function deptColors(names: string[]): Record<string, string> {
  const out: Record<string, string> = {};
  const sorted = [...new Set(names)].filter((n) => n !== "Unassigned" && n !== "(none)").sort();
  sorted.forEach((n, i) => (out[n] = SLOTS[i] ?? "var(--s-other)"));
  out.Unassigned = "var(--s-other)";
  out["(none)"] = "var(--s-other)";
  return out;
}

export function useDeptColors(): Record<string, string> {
  const q = useOrg(30);
  const names = q.data?.departments.map((d) => d.name).join("|") ?? "";
  return useMemo(() => deptColors(names ? names.split("|") : []), [names]);
}

export function DeptDot({ color }: { color?: string }) {
  return <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: color ?? "var(--s-other)" }} />;
}

// ---- small displays -------------------------------------------------------------------------

/** "▲ 8.5%" next to a number. Spend going up is not good or bad by itself, so tone is opt-in. */
export function Delta({
  cur,
  prev,
  good,
  className,
  title,
}: {
  cur: number | null | undefined;
  prev: number | null | undefined;
  /** Which direction reads as good: colors the arrow. Omit for a neutral delta. */
  good?: "up" | "down";
  className?: string;
  title?: string;
}) {
  const d = delta(cur, prev);
  return <DeltaText dir={d.dir} text={d.text} good={good} className={className} title={title ?? (prev !== null && prev !== undefined ? `previous period ${money(prev)}` : undefined)} />;
}

export function DeltaText({ dir, text, good, className, title }: { dir: DeltaDir; text: string; good?: "up" | "down"; className?: string; title?: string }) {
  const tone = !good || dir === "flat" || dir === "new" ? "text-muted" : (dir === "up") === (good === "up") ? "text-good" : "text-serious";
  return (
    <span className={cx("tnum whitespace-nowrap text-xs font-medium", tone, className)} title={title}>
      {text}
    </span>
  );
}

/** Adherence with a tone: under 95% warns, under 90% is bad. */
export function AdherenceValue({ v, className }: { v: number | null | undefined; className?: string }) {
  const tone = v === null || v === undefined ? "text-muted" : v < 0.9 ? "text-bad" : v < 0.95 ? "text-warn" : "text-ink";
  return <span className={cx("tnum", tone, className)}>{pctAuto(v)}</span>;
}

/** A count that only draws attention when it is not zero. */
export function AlertCount({ n, tone = "bad", suffix }: { n: number | null | undefined; tone?: "bad" | "warn"; suffix?: string }) {
  if (!n) return <span className="tnum text-muted">0</span>;
  return (
    <span className={cx("tnum inline-flex items-center gap-1 font-semibold", tone === "bad" ? "text-bad" : "text-warn")}>
      <span className={cx("h-1.5 w-1.5 rounded-full", tone === "bad" ? "bg-bad" : "bg-warn")} />
      {count(n)}
      {suffix && <span className="font-normal text-muted">{suffix}</span>}
    </span>
  );
}

export function ShareBar({ value, max, color = "var(--s1)" }: { value: number; max: number; color?: string }) {
  const w = max > 0 ? Math.max((value / max) * 100, value > 0 ? 2 : 0) : 0;
  return (
    <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-ink/[0.07]">
      <div className="h-full rounded-full" style={{ width: `${w}%`, background: color }} />
    </div>
  );
}

export function Breadcrumbs({ items }: { items: { label: ReactNode; to?: string }[] }) {
  return (
    <nav aria-label="Breadcrumb" className="flex min-w-0 flex-wrap items-center gap-1 text-xs text-muted">
      {items.map((it, i) => (
        <span key={i} className="flex min-w-0 items-center gap-1">
          {i > 0 && <IconChevronRight size={12} className="shrink-0 opacity-60" />}
          {it.to ? (
            <Link to={it.to} className="truncate hover:text-ink">
              {it.label}
            </Link>
          ) : (
            <span className="truncate text-ink2">{it.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}

/** "1–50 of 5,012" with previous/next. */
export function Pager({ offset, limit, total, onChange }: { offset: number; limit: number; total: number; onChange: (offset: number) => void }) {
  if (total <= 0) return null;
  const from = Math.min(offset + 1, total);
  const to = Math.min(offset + limit, total);
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-3 py-2 text-xs text-muted">
      <span className="tnum">
        {count(from)}–{count(to)} of <span className="font-medium text-ink2">{total.toLocaleString("en-US")}</span>
      </span>
      <div className="flex items-center gap-1.5">
        <Button size="sm" variant="ghost" disabled={offset <= 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          Previous
        </Button>
        <span className="tnum">
          page {page.toLocaleString("en-US")} / {pages.toLocaleString("en-US")}
        </span>
        <Button size="sm" variant="ghost" disabled={to >= total} onClick={() => onChange(offset + limit)}>
          Next
        </Button>
      </div>
    </div>
  );
}

export function SortTh<K extends string>({
  k,
  sort,
  order,
  onSort,
  children,
  right,
  title,
}: {
  k: K;
  sort: K | null;
  order: "asc" | "desc";
  onSort?: (k: K) => void;
  children: ReactNode;
  right?: boolean;
  title?: string;
}) {
  const active = sort === k;
  if (!onSort)
    return (
      <th className={right ? "text-right" : undefined} title={title}>
        {children}
      </th>
    );
  return (
    <th className={right ? "text-right" : undefined} aria-sort={active ? (order === "asc" ? "ascending" : "descending") : undefined} title={title}>
      <button
        type="button"
        onClick={() => onSort(k)}
        className={cx("inline-flex items-center gap-1 uppercase tracking-wide hover:text-ink", active && "text-ink")}
      >
        {children}
        <span className={cx("text-[9px]", !active && "opacity-0")}>{order === "asc" ? "▲" : "▼"}</span>
      </button>
    </th>
  );
}

// ---- unit tables (departments, teams) -------------------------------------------------------

export type UnitSortKey = "name" | "headcount" | "active" | "usd" | "delta" | "usd_per_active" | "adherence" | "interventions" | "incidents" | "risk";

function unitSortValue(r: UnitRow, k: UnitSortKey): number | string {
  switch (k) {
    case "name":
      return r.name.toLowerCase();
    case "active":
      return r.headcount ? r.active / r.headcount : 0;
    case "delta":
      return r.usd_prev ? (r.usd - r.usd_prev) / r.usd_prev : r.usd ? Infinity : 0;
    case "adherence":
      return r.adherence ?? 1;
    case "incidents":
      return r.incidents_open;
    case "risk":
      return r.people_at_risk * 1000 + r.incidents_open;
    case "usd_per_active":
      return r.usd_per_active ?? 0;
    default:
      return r[k];
  }
}

export function sortUnits(rows: UnitRow[], k: UnitSortKey, order: "asc" | "desc"): UnitRow[] {
  const dir = order === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = unitSortValue(a, k);
    const y = unitSortValue(b, k);
    return (x < y ? -1 : x > y ? 1 : 0) * dir || a.name.localeCompare(b.name);
  });
}

/**
 * Departments or teams as rows. Sorting is local unless `onSort` is given (server-side); with server sorting,
 * only the keys in `sortable` get a header button.
 */
export function UnitTable({
  rows,
  kind,
  colors,
  showDepartment,
  sort: extSort,
  order: extOrder,
  onSort,
  sortable,
  totalUsd,
  compact,
  empty = "No units",
}: {
  /** Six columns only: name, people, spend + Δ, $/active, adherence, risk. */
  compact?: boolean;
  rows: UnitRow[];
  kind: "department" | "team";
  colors?: Record<string, string>;
  showDepartment?: boolean;
  sort?: UnitSortKey;
  order?: "asc" | "desc";
  onSort?: (k: UnitSortKey) => void;
  sortable?: UnitSortKey[];
  totalUsd?: number;
  empty?: string;
}) {
  const nav = useNavigate();
  const [lsort, setLsort] = useState<UnitSortKey>("usd");
  const [lorder, setLorder] = useState<"asc" | "desc">("desc");
  const server = !!onSort;
  const sort = server ? (extSort ?? null) : lsort;
  const order = server ? (extOrder ?? "desc") : lorder;
  const shown = server ? rows : sortUnits(rows, lsort, lorder);
  const handle = (k: UnitSortKey) => {
    if (server) return onSort!(k);
    if (k === lsort) setLorder((o) => (o === "asc" ? "desc" : "asc"));
    else {
      setLsort(k);
      setLorder(k === "name" ? "asc" : k === "adherence" ? "asc" : "desc");
    }
  };
  const can = (k: UnitSortKey) => (!server || !sortable || sortable.includes(k) ? handle : undefined);
  const maxUsd = Math.max(...rows.map((r) => r.usd), 0);
  if (!rows.length) return <Empty title={empty} />;
  const color = (r: UnitRow) => colors?.[kind === "department" ? r.name : (r.department ?? "")];
  if (compact) return <CompactUnitTable rows={shown} kind={kind} color={color} sort={sort} order={order} can={can} maxUsd={maxUsd} />;
  return (
    <TableWrap>
      <table className="tbl min-w-[980px]">
        <thead>
          <tr>
            <SortTh k="name" sort={sort} order={order} onSort={can("name")}>
              {kind === "department" ? "Department" : "Team"}
            </SortTh>
            <SortTh k="headcount" sort={sort} order={order} onSort={can("headcount")} right>
              People
            </SortTh>
            <SortTh k="active" sort={sort} order={order} onSort={can("active")} right title="People with metered AI usage in the window">
              Active
            </SortTh>
            <SortTh k="usd" sort={sort} order={order} onSort={can("usd")} right>
              Spend
            </SortTh>
            <SortTh k="delta" sort={sort} order={order} onSort={can("delta")} right title="Against the previous window of the same length">
              Δ prev
            </SortTh>
            <SortTh k="usd_per_active" sort={sort} order={order} onSort={can("usd_per_active")} right>
              $ / active
            </SortTh>
            <SortTh k="adherence" sort={sort} order={order} onSort={can("adherence")} right title="Share of policy checks that needed no intervention">
              Adherence
            </SortTh>
            <SortTh k="interventions" sort={sort} order={order} onSort={can("interventions")} right title="Checks that were warned, redacted or blocked">
              Interventions
            </SortTh>
            <SortTh k="incidents" sort={sort} order={order} onSort={can("incidents")} right>
              Open incidents
            </SortTh>
            <SortTh k="risk" sort={sort} order={order} onSort={can("risk")} right>
              At risk
            </SortTh>
          </tr>
        </thead>
        <tbody>
          {shown.map((r) => (
            <tr
              key={`${r.department ?? ""}/${r.name}`}
              className="row-link"
              onClick={() => nav(kind === "department" ? orgPath.department(r.name) : orgPath.team(r.name))}
            >
              <td className="max-w-[260px]">
                <div className="flex min-w-0 items-center gap-2">
                  {colors && <DeptDot color={color(r)} />}
                  <span className="truncate font-medium text-ink">{r.name}</span>
                </div>
                <div className="truncate pl-[18px] text-[11px] text-muted">
                  {kind === "department"
                    ? [r.teams !== undefined ? `${r.teams} teams` : null, r.owner ? `owner ${r.owner}` : null, r.top_workflow ? `top: ${r.top_workflow}` : null]
                        .filter(Boolean)
                        .join(" · ")
                    : [showDepartment ? r.department : null, r.top_workflow ? `top: ${r.top_workflow}` : null].filter(Boolean).join(" · ")}
                </div>
              </td>
              <td className="tnum text-right">{count(r.headcount)}</td>
              <td className="tnum text-right">
                <div>{share(r.headcount ? r.active / r.headcount : null)}</div>
                <div className="text-[11px] text-muted">{count(r.active)}</div>
              </td>
              <td className="tnum min-w-[110px] text-right">
                <div className="font-medium text-ink">{money(r.usd)}</div>
                <ShareBar value={r.usd} max={maxUsd} color={color(r)} />
                {totalUsd ? <div className="text-[10.5px] text-muted">{share(r.usd / totalUsd)} of org</div> : null}
              </td>
              <td className="text-right">
                <Delta cur={r.usd} prev={r.usd_prev} />
              </td>
              <td className="tnum text-right">{unitMoney(r.usd_per_active)}</td>
              <td className="text-right">
                <AdherenceValue v={r.adherence} />
              </td>
              <td className="tnum text-right text-ink2">{count(r.interventions)}</td>
              <td className="text-right">
                <AlertCount n={r.incidents_open} />
              </td>
              <td className="text-right">
                <AlertCount n={r.people_at_risk} tone="warn" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

function CompactUnitTable({
  rows,
  kind,
  color,
  sort,
  order,
  can,
  maxUsd,
}: {
  rows: UnitRow[];
  kind: "department" | "team";
  color: (r: UnitRow) => string | undefined;
  sort: UnitSortKey | null;
  order: "asc" | "desc";
  can: (k: UnitSortKey) => ((k: UnitSortKey) => void) | undefined;
  maxUsd: number;
}) {
  const nav = useNavigate();
  return (
    <TableWrap>
      <table className="tbl min-w-[640px]">
        <thead>
          <tr>
            <SortTh k="name" sort={sort} order={order} onSort={can("name")}>
              {kind === "department" ? "Department" : "Team"}
            </SortTh>
            <SortTh k="headcount" sort={sort} order={order} onSort={can("headcount")} right>
              People
            </SortTh>
            <SortTh k="usd" sort={sort} order={order} onSort={can("usd")} right>
              Spend
            </SortTh>
            <SortTh k="usd_per_active" sort={sort} order={order} onSort={can("usd_per_active")} right>
              $ / active
            </SortTh>
            <SortTh k="adherence" sort={sort} order={order} onSort={can("adherence")} right>
              Adherence
            </SortTh>
            <SortTh k="risk" sort={sort} order={order} onSort={can("risk")} right title="People past the risk alert line · open incidents">
              Risk
            </SortTh>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.department ?? ""}/${r.name}`} className="row-link" onClick={() => nav(kind === "department" ? orgPath.department(r.name) : orgPath.team(r.name))}>
              <td className="py-3">
                <div className="flex min-w-0 items-center gap-2">
                  {color(r) && <DeptDot color={color(r)} />}
                  <span className="truncate font-medium text-ink">{r.name}</span>
                </div>
                {kind === "team" && r.department && <div className="truncate pl-[18px] text-[11px] text-muted">{r.department}</div>}
              </td>
              <td className="tnum text-right">
                {count(r.headcount)}
                <div className="text-[11px] text-muted">{share(r.headcount ? r.active / r.headcount : null)} active</div>
              </td>
              <td className="tnum min-w-[140px] text-right">
                <span className="font-semibold text-ink">{money(r.usd)}</span> <Delta cur={r.usd} prev={r.usd_prev} />
                <ShareBar value={r.usd} max={maxUsd} color={color(r)} />
              </td>
              <td className="tnum text-right">{unitMoney(r.usd_per_active)}</td>
              <td className="text-right">
                <AdherenceValue v={r.adherence} />
              </td>
              <td className="text-right">
                {r.people_at_risk || r.incidents_open ? (
                  <span className="inline-flex items-center gap-2">
                    <AlertCount n={r.people_at_risk} tone="warn" suffix=" at risk" />
                    {r.incidents_open > 0 && <AlertCount n={r.incidents_open} suffix=" open" />}
                  </span>
                ) : (
                  <span className="text-muted">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

// ---- outliers -------------------------------------------------------------------------------

function PersonCell({ p }: { p: { principal: string; name?: string; team: string; department: string } }) {
  return (
    <div className="min-w-0">
      <Link to={orgPath.person(p.principal)} className="block truncate font-medium text-ink hover:text-accent">
        {p.name || p.principal}
      </Link>
      <div className="truncate text-[11px] text-muted">
        <Link to={orgPath.team(p.team)} className="hover:text-ink">
          {p.team}
        </Link>
        {" · "}
        <Link to={orgPath.department(p.department)} className="hover:text-ink">
          {p.department}
        </Link>
      </div>
    </div>
  );
}

type OutlierTab = "cost" | "risk" | "growth";

/** The only place individuals appear above team level: top-N lists, each a link into the person's page. */
export function OutliersList({
  cost,
  risk,
  growth,
  days,
  limit = 10,
  more,
}: {
  cost: CostOutlier[];
  risk: RiskOutlier[];
  growth?: GrowthOutlier[];
  days: number;
  limit?: number;
  more?: ReactNode;
}) {
  const [tab, setTab] = useState<OutlierTab>("cost");
  const tabs: { value: OutlierTab; label: string }[] = [
    { value: "cost", label: `Cost (${cost.length})` },
    { value: "risk", label: `Risk (${risk.length})` },
  ];
  if (growth) tabs.push({ value: "growth", label: `Growth (${growth.length})` });
  const hint =
    tab === "cost"
      ? `Spend at least 3× their team's median, last ${days} days`
      : tab === "risk"
        ? "Highest risk scores right now"
        : `Fastest spend growth vs the previous ${days} days`;
  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2">
        <Segmented<OutlierTab> value={tab} onChange={setTab} options={tabs} />
        <span className="text-[11px] text-muted">{hint}</span>
      </div>
      {tab === "cost" &&
        (cost.length ? (
          <ul className="divide-y divide-line/60">
            {cost.slice(0, limit).map((p, i) => (
              <li key={p.principal} className="grid grid-cols-[20px_minmax(0,1fr)_auto] items-center gap-2 px-3 py-2 text-xs">
                <span className="tnum text-muted">{i + 1}</span>
                <PersonCell p={p} />
                <div className="text-right">
                  <div className="tnum font-semibold text-ink">{money(p.usd)}</div>
                  <div className="tnum text-[11px] text-serious" title={`team median ${money(p.team_median)}`}>
                    {times(p.ratio)} team median
                  </div>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <Empty title="No cost outliers" hint="Nobody spends 3× their team's median." />
        ))}
      {tab === "risk" &&
        (risk.length ? (
          <ul className="divide-y divide-line/60">
            {risk.slice(0, limit).map((p, i) => (
              <li key={p.principal} className="grid grid-cols-[20px_minmax(0,1fr)_auto] items-center gap-2 px-3 py-2 text-xs">
                <span className="tnum text-muted">{i + 1}</span>
                <PersonCell p={p} />
                <div className="flex flex-col items-end gap-0.5">
                  <span className="tnum font-semibold text-ink">risk {Math.round(p.risk)}</span>
                  <span className="flex items-center gap-1">
                    {p.open_incidents > 0 && <span className="tnum text-[11px] text-bad">{p.open_incidents} open</span>}
                    <LevelPill level={p.level} />
                  </span>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <Empty title="Nobody at risk" />
        ))}
      {tab === "growth" &&
        growth &&
        (growth.length ? (
          <ul className="divide-y divide-line/60">
            {growth.slice(0, limit).map((p, i) => (
              <li key={p.principal} className="grid grid-cols-[20px_minmax(0,1fr)_auto] items-center gap-2 px-3 py-2 text-xs">
                <span className="tnum text-muted">{i + 1}</span>
                <PersonCell p={p} />
                <div className="text-right">
                  <div className="tnum font-semibold text-ink">{money(p.usd)}</div>
                  <div className="tnum text-[11px] text-muted">
                    from {money(p.usd_prev)} · <Delta cur={p.usd} prev={p.usd_prev} className="text-[11px]" />
                  </div>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <Empty title="No fast growers" />
        ))}
      {more && <div className="border-t border-line px-3 py-2 text-right text-xs">{more}</div>}
    </div>
  );
}

// ---- team treemap ---------------------------------------------------------------------------

export type MapColor = "growth" | "adherence";

interface TmNode {
  name: string;
  size: number;
  department: string;
  row: UnitRow;
  /** -1..1 on the diverging scale. */
  t: number;
  [k: string]: unknown;
}

function tmScale(r: UnitRow, mode: MapColor, adhMid: number): number {
  if (mode === "growth") {
    if (!r.usd_prev) return r.usd ? 1 : 0;
    const g = (r.usd - r.usd_prev) / r.usd_prev;
    return Math.max(-1, Math.min(1, g / 0.5)); // ±50% saturates
  }
  if (r.adherence === null || r.adherence === undefined) return 0;
  // Below the org's adherence is warm, above is cool; 2 pp away saturates.
  return Math.max(-1, Math.min(1, (adhMid - r.adherence) / 0.02));
}

function tmFill(t: number): string {
  const pct = Math.round(Math.abs(t) * 85);
  const pole = t >= 0 ? "var(--div-warm)" : "var(--div-cool)";
  return `color-mix(in oklab, ${pole} ${pct}%, var(--div-mid))`;
}

function TmCell(props: Record<string, unknown> & { onPick: (n: string) => void; mode: MapColor }) {
  const { x, y, width, height, depth, name, onPick, mode } = props as { x: number; y: number; width: number; height: number; depth: number; name: string; onPick: (n: string) => void; mode: MapColor };
  const node = props as unknown as TmNode;
  if (depth !== 1 || width <= 0 || height <= 0) return null;
  const strong = Math.abs(node.t) > 0.55;
  const fg = strong ? "#ffffff" : "rgb(var(--ink))";
  const big = width > 70 && height > 34;
  const g = node.row.usd_prev ? (node.row.usd - node.row.usd_prev) / node.row.usd_prev : null;
  return (
    <g onClick={() => onPick(name)} style={{ cursor: "pointer" }}>
      <rect x={x} y={y} width={width} height={height} style={{ fill: tmFill(node.t), stroke: "var(--chart-surface)", strokeWidth: 2 }} rx={3} />
      {big && (
        <>
          <text x={x + 6} y={y + 15} fontSize={11} fontWeight={600} style={{ fill: fg }}>
            {name.length * 6.5 > width - 10 ? name.slice(0, Math.max(3, Math.floor((width - 14) / 6.5))) + "…" : name}
          </text>
          {height > 44 && (
            <text x={x + 6} y={y + 29} fontSize={10.5} style={{ fill: fg, opacity: 0.85 }}>
              {money(node.row.usd)}
              {width > 120 ? ` · ${mode === "growth" ? (g === null ? "new" : `${g >= 0 ? "+" : ""}${(g * 100).toFixed(0)}%`) : pctAuto(node.row.adherence)}` : ""}
            </text>
          )}
        </>
      )}
    </g>
  );
}

function TmTooltip({ active, payload, mode }: { active?: boolean; payload?: { payload: TmNode }[]; mode: MapColor }) {
  if (!active || !payload?.length) return null;
  const n = payload[0].payload;
  if (!n?.row) return null;
  const r = n.row;
  return (
    <div className="min-w-[200px] rounded-lg border border-line bg-panel px-3 py-2 text-xs shadow-xl">
      <div className="font-medium text-ink">{r.name}</div>
      <div className="mb-1.5 text-[11px] text-muted">{r.department}</div>
      <div className="grid grid-cols-[auto_auto] gap-x-4 gap-y-0.5">
        <span className="text-muted">Spend</span>
        <span className="tnum text-right text-ink">
          {money(r.usd)} <Delta cur={r.usd} prev={r.usd_prev} className="text-[11px]" />
        </span>
        <span className="text-muted">People</span>
        <span className="tnum text-right text-ink">
          {count(r.active)} active / {count(r.headcount)}
        </span>
        <span className="text-muted">$ / active</span>
        <span className="tnum text-right text-ink">{unitMoney(r.usd_per_active)}</span>
        <span className={cx("text-muted", mode === "adherence" && "font-medium text-ink2")}>Adherence</span>
        <span className="tnum text-right text-ink">{pctAuto(r.adherence)}</span>
        <span className="text-muted">Incidents</span>
        <span className="tnum text-right text-ink">{r.incidents_open} open</span>
      </div>
    </div>
  );
}

/** Teams as area = spend, color = growth vs previous window or adherence vs the org (diverging, gray = typical). */
export function TeamTreemap({ teams, mode, orgAdherence, height = 340 }: { teams: UnitRow[]; mode: MapColor; orgAdherence: number | null; height?: number }) {
  const nav = useNavigate();
  const data = useMemo<TmNode[]>(
    () =>
      teams
        .filter((r) => r.usd > 0)
        .map((r) => ({ name: r.name, size: r.usd, department: r.department ?? "", row: r, t: tmScale(r, mode, orgAdherence ?? 0.98) })),
    [teams, mode, orgAdherence],
  );
  if (!data.length) return <Empty title="No team spend in this period" />;
  return (
    <div>
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <Treemap data={data} dataKey="size" isAnimationActive={false} content={<TmCell onPick={(n: string) => nav(orgPath.team(n))} mode={mode} />}>
            <Tooltip content={<TmTooltip mode={mode} />} />
          </Treemap>
        </ResponsiveContainer>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
        <span>Area = spend</span>
        <span className="flex items-center gap-1.5">
          {mode === "growth" ? "shrinking" : "above org adherence"}
          <span className="flex overflow-hidden rounded-sm">
            {[-1, -0.5, 0, 0.5, 1].map((t) => (
              <span key={t} className="h-2.5 w-5" style={{ background: tmFill(t) }} />
            ))}
          </span>
          {mode === "growth" ? "growing (±50% saturates)" : `below org ${pctAuto(orgAdherence)} (2 pp saturates)`}
        </span>
      </div>
    </div>
  );
}

// ---- global search --------------------------------------------------------------------------

function useDebounced<T>(v: T, ms = 200): T {
  const [d, setD] = useState(v);
  useEffect(() => {
    const t = setTimeout(() => setD(v), ms);
    return () => clearTimeout(t);
  }, [v, ms]);
  return d;
}
export { useDebounced };

interface Hit {
  kind: "person" | "team" | "department" | "all";
  key: string;
  label: string;
  sub: string;
  to: string;
}

/** People, teams and departments by name; Enter with nothing picked opens the full paginated search. */
export function GlobalSearch({ onNavigate }: { onNavigate?: () => void }) {
  const nav = useNavigate();
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState(-1);
  const q = useDebounced(text.trim(), 180);
  const box = useRef<HTMLDivElement>(null);
  const people = useQuery({
    queryKey: ["admin", "people-search", "quick", q],
    queryFn: () => org.people({ q, limit: 6, sort: "usd" }),
    enabled: q.length >= 2,
    staleTime: 15_000,
  });
  const teams = useQuery({ queryKey: ["admin", "org", "teams", "all-names"], queryFn: () => org.teams({ limit: 200, sort: "usd" }), staleTime: 60_000, enabled: open });
  const o = useOrg(30);

  const hits: Hit[] = useMemo(() => {
    if (q.length < 1) return [];
    const ql = q.toLowerCase();
    const out: Hit[] = [];
    for (const d of o.data?.departments ?? [])
      if (d.name.toLowerCase().includes(ql))
        out.push({ kind: "department", key: `d:${d.name}`, label: d.name, sub: `Department · ${count(d.headcount)} people`, to: orgPath.department(d.name) });
    for (const t of (teams.data?.rows ?? []).filter((t) => t.name.toLowerCase().includes(ql)).slice(0, 4))
      out.push({ kind: "team", key: `t:${t.name}`, label: t.name, sub: `Team · ${t.department ?? ""} · ${count(t.headcount)} people`, to: orgPath.team(t.name) });
    for (const p of people.data?.rows ?? [])
      out.push({ kind: "person", key: `p:${p.principal}`, label: p.name || p.principal, sub: `${p.principal} · ${p.team} · ${p.department}`, to: orgPath.person(p.principal) });
    out.push({
      kind: "all",
      key: "all",
      label: `Search people for “${q}”`,
      sub: people.data ? `${people.data.total.toLocaleString("en-US")} match${people.data.total === 1 ? "" : "es"}` : "paginated results",
      to: orgPath.people({ q }),
    });
    return out;
  }, [q, o.data, teams.data, people.data]);

  useEffect(() => setSel(-1), [q]);
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  const go = (to: string) => {
    nav(to);
    setOpen(false);
    setText("");
    onNavigate?.();
  };

  return (
    <div ref={box} className="relative">
      <label className="flex items-center gap-2 rounded-md border border-line bg-page px-2.5 focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/30">
        <IconSearch size={14} className="shrink-0 text-muted" />
        <input
          className="h-8 w-full min-w-0 bg-transparent text-sm text-ink placeholder:text-muted focus:outline-none"
          placeholder="People, teams, departments"
          aria-label="Search people, teams and departments"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setSel((s) => Math.min(s + 1, hits.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setSel((s) => Math.max(s - 1, -1));
            } else if (e.key === "Enter") {
              if (sel >= 0 && hits[sel]) go(hits[sel].to);
              else if (text.trim()) go(orgPath.people({ q: text.trim() }));
            } else if (e.key === "Escape") setOpen(false);
          }}
        />
      </label>
      {open && q.length >= 1 && (
        <div className="absolute left-0 right-0 top-full z-50 mt-1 max-h-[60vh] min-w-[260px] overflow-y-auto rounded-lg border border-line bg-panel py-1 shadow-2xl">
          {hits.map((h, i) => (
            <button
              key={h.key}
              type="button"
              onMouseEnter={() => setSel(i)}
              onClick={() => go(h.to)}
              className={cx("block w-full px-3 py-1.5 text-left", i === sel ? "bg-raised" : "hover:bg-raised/60", h.kind === "all" && "border-t border-line")}
            >
              <div className={cx("truncate text-sm", h.kind === "all" ? "text-accent" : "text-ink")}>{h.label}</div>
              <div className="truncate text-[11px] text-muted">{h.sub}</div>
            </button>
          ))}
          {people.isFetching && <div className="px-3 py-1 text-[11px] text-muted">Searching…</div>}
        </div>
      )}
    </div>
  );
}
