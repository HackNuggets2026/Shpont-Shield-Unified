import { useMemo, useState, type ReactNode } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipProps,
} from "recharts";
import type { Timeseries } from "../api";
import { dayLabel, pct, tokens as fmtTokens, usd, usdTick } from "../lib/format";
import { cx, Empty } from "./ui";

// Categorical slots, fixed order (validated palette, see index.css). Past 7 series, the rest fold into "Other".
const SLOTS = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--s6)", "var(--s7)"];
const OTHER = "var(--s-other)";
const MAX_SERIES = 7;

export function seriesLabel(k: string): string {
  if (k === "(none)") return "Unattributed";
  if (k === "unlabeled") return "Unlabeled";
  return k;
}

interface Folded {
  keys: string[];
  colors: Record<string, string>;
  rows: Record<string, number | string>[];
}

function fold(ts: Timeseries): Folded {
  const entries = Object.entries(ts.series); // server orders largest first
  const head = entries.slice(0, entries.length > MAX_SERIES + 1 ? MAX_SERIES : MAX_SERIES + 1);
  const tail = entries.slice(head.length);
  const keys = head.map(([k]) => k);
  const colors: Record<string, string> = {};
  keys.forEach((k, i) => (colors[k] = SLOTS[i] ?? OTHER));
  if (tail.length) {
    keys.push("Other");
    colors.Other = OTHER;
  }
  const rows = ts.days.map((d, i) => {
    const row: Record<string, number | string> = { day: d };
    for (const [k, v] of head) row[k] = v[i];
    if (tail.length) row.Other = tail.reduce((s, [, v]) => s + v[i], 0);
    return row;
  });
  return { keys, colors, rows };
}

type Fmt = (v: number) => string;

function ChartTooltip({ active, payload, label, fmt }: TooltipProps<number, string> & { fmt: Fmt }) {
  if (!active || !payload?.length) return null;
  const items = payload.filter((p) => (p.value ?? 0) > 0).sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
  const total = payload.reduce((s, p) => s + (p.value ?? 0), 0);
  return (
    <div className="min-w-[180px] rounded-lg border border-line bg-panel px-3 py-2 text-xs shadow-xl">
      <div className="mb-1.5 flex justify-between gap-4 font-medium text-ink">
        <span>{dayLabel(String(label))}</span>
        <span className="tnum">{fmt(total)}</span>
      </div>
      {items.length === 0 && <div className="text-muted">No usage</div>}
      {items.slice(0, 10).map((p) => (
        <div key={p.dataKey as string} className="flex items-center justify-between gap-4 py-0.5">
          <span className="flex min-w-0 items-center gap-1.5 text-ink2">
            <span className="h-2 w-2 shrink-0 rounded-sm" style={{ background: p.color }} />
            <span className="truncate">{seriesLabel(String(p.dataKey))}</span>
          </span>
          <span className="tnum text-ink">{fmt(p.value ?? 0)}</span>
        </div>
      ))}
    </div>
  );
}

export function Legend({
  keys,
  colors,
  totals,
  fmt,
  hidden,
  onToggle,
}: {
  keys: string[];
  colors: Record<string, string>;
  totals?: Record<string, number>;
  fmt?: Fmt;
  hidden?: Set<string>;
  onToggle?: (k: string) => void;
}) {
  return (
    <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
      {keys.map((k) => (
        <button
          key={k}
          type="button"
          onClick={() => onToggle?.(k)}
          className={cx("flex items-center gap-1.5 text-ink2 hover:text-ink", hidden?.has(k) && "opacity-40")}
        >
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: colors[k] }} />
          <span>{seriesLabel(k)}</span>
          {totals && fmt && <span className="tnum text-muted">{fmt(totals[k] ?? 0)}</span>}
        </button>
      ))}
    </div>
  );
}

/** Daily totals stacked by a dimension: area (trend) or bar (per day). */
export function StackedChart({
  ts,
  kind = "bar",
  metric = "usd",
  height = 260,
}: {
  ts: Timeseries;
  kind?: "bar" | "area";
  metric?: "usd" | "tokens" | "events";
  height?: number;
}) {
  const { keys, colors, rows } = useMemo(() => fold(ts), [ts]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const fmt: Fmt = metric === "usd" ? (v) => usd(v) : metric === "tokens" ? fmtTokens : (v) => String(Math.round(v));
  const tick: Fmt = metric === "usd" ? usdTick : metric === "tokens" ? fmtTokens : (v) => String(v);
  const totals = useMemo(() => {
    const t: Record<string, number> = {};
    for (const r of rows) for (const k of keys) t[k] = (t[k] ?? 0) + Number(r[k] ?? 0);
    return t;
  }, [rows, keys]);

  if (!keys.length || ts.totals.every((v) => !v)) {
    return <Empty title="No usage in this period" hint="Spend appears here as soon as traffic flows through the gateway or a meter reports." />;
  }
  const visible = keys.filter((k) => !hidden.has(k));
  const toggle = (k: string) =>
    setHidden((h) => {
      const n = new Set(h);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });

  const axes = (
    <>
      <CartesianGrid vertical={false} stroke="var(--grid)" />
      <XAxis
        dataKey="day"
        tickFormatter={dayLabel}
        tick={{ fill: "var(--axis)", fontSize: 11 }}
        axisLine={{ stroke: "var(--grid)" }}
        tickLine={false}
        minTickGap={24}
      />
      <YAxis tickFormatter={tick} tick={{ fill: "var(--axis)", fontSize: 11 }} axisLine={false} tickLine={false} width={52} />
      <Tooltip content={<ChartTooltip fmt={fmt} />} cursor={{ fill: "rgb(var(--ink) / 0.05)", stroke: "rgb(var(--ink) / 0.2)" }} />
    </>
  );

  return (
    <div>
      <div style={{ height }} className="-ml-2">
        <ResponsiveContainer width="100%" height="100%">
          {kind === "area" ? (
            <AreaChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
              {axes}
              {visible.map((k) => (
                <Area
                  key={k}
                  type="monotone"
                  dataKey={k}
                  stackId="1"
                  stroke={colors[k]}
                  strokeWidth={1.5}
                  fill={colors[k]}
                  fillOpacity={0.35}
                  isAnimationActive={false}
                />
              ))}
            </AreaChart>
          ) : (
            <BarChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: 0 }} barCategoryGap="18%">
              {axes}
              {visible.map((k, i) => (
                <Bar
                  key={k}
                  dataKey={k}
                  stackId="1"
                  fill={colors[k]}
                  stroke="var(--chart-surface)"
                  strokeWidth={1}
                  radius={i === visible.length - 1 ? [3, 3, 0, 0] : 0}
                  isAnimationActive={false}
                />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
      <Legend keys={keys} colors={colors} totals={totals} fmt={fmt} hidden={hidden} onToggle={toggle} />
    </div>
  );
}

/** Horizontal labelled bars in HTML: legible at any width, values always printed. */
export function BarList({
  rows,
  fmt,
  max,
  tone = "var(--s1)",
  right,
  empty = "Nothing to show yet",
}: {
  rows: { key: string; value: number; label?: ReactNode; color?: string; sub?: ReactNode }[];
  fmt: (v: number) => string;
  max?: number;
  tone?: string;
  right?: (r: { key: string; value: number }) => ReactNode;
  empty?: string;
}) {
  if (!rows.length) return <Empty title={empty} />;
  const top = max ?? Math.max(...rows.map((r) => r.value), 0);
  return (
    <ul className="space-y-2.5">
      {rows.map((r) => (
        <li key={r.key} className="text-xs">
          <div className="mb-1 flex items-baseline justify-between gap-3">
            <span className="min-w-0 truncate font-medium text-ink2">{r.label ?? seriesLabel(r.key)}</span>
            <span className="tnum shrink-0 text-ink">{right ? right(r) : fmt(r.value)}</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-ink/[0.07]">
            <div
              className="h-full rounded-full"
              style={{ width: `${top > 0 ? Math.max((r.value / top) * 100, r.value > 0 ? 1 : 0) : 0}%`, background: r.color ?? tone }}
            />
          </div>
          {r.sub && <div className="mt-0.5 text-[11px] text-muted">{r.sub}</div>}
        </li>
      ))}
    </ul>
  );
}

/** Adherence by group: share of checks needing no intervention, worst first. */
export function AdherenceBars({ rows }: { rows: { key: string; adherence: number | null; total: number; block: number; warn: number; redact: number }[] }) {
  const sorted = [...rows].filter((r) => r.total > 0).sort((a, b) => (a.adherence ?? 1) - (b.adherence ?? 1));
  return (
    <BarList
      rows={sorted.map((r) => ({
        key: r.key,
        value: r.adherence ?? 0,
        color: (r.adherence ?? 1) < 0.9 ? "#d03b3b" : (r.adherence ?? 1) < 0.97 ? "#fab219" : "var(--s3)",
        sub: `${r.total.toLocaleString()} checks · ${r.block} blocked · ${r.warn + r.redact} warned/redacted`,
      }))}
      max={1}
      fmt={(v) => pct(v)}
      empty="No policy checks in this period"
    />
  );
}

/** Tiny inline trend line for tables and tiles. */
export function Sparkline({ values, color = "var(--s1)", height = 28 }: { values: number[]; color?: string; height?: number }) {
  if (!values.length || values.every((v) => !v)) return <div style={{ height }} />;
  const data = values.map((v, i) => ({ i, v }));
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
          <Area type="monotone" dataKey="v" stroke={color} strokeWidth={1.5} fill={color} fillOpacity={0.15} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
