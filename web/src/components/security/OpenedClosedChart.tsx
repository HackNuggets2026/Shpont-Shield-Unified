import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipProps } from "recharts";
import { dayLabel } from "../../lib/format";
import { Empty } from "../ui";

const OPENED = "var(--s2)";
const CLOSED = "var(--s1)";

function Tip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const o = Number(payload.find((p) => p.dataKey === "opened")?.value ?? 0);
  const c = Number(payload.find((p) => p.dataKey === "closed")?.value ?? 0);
  return (
    <div className="min-w-[150px] rounded-lg border border-line bg-panel px-3 py-2 text-xs shadow-xl">
      <div className="mb-1 font-medium text-ink">{dayLabel(String(label))}</div>
      <div className="flex justify-between gap-4 text-ink2">
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm" style={{ background: OPENED }} /> Opened
        </span>
        <span className="tnum text-ink">{o}</span>
      </div>
      <div className="flex justify-between gap-4 text-ink2">
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-sm" style={{ background: CLOSED }} /> Closed
        </span>
        <span className="tnum text-ink">{c}</span>
      </div>
      <div className="mt-1 border-t border-line pt-1 text-muted">net {o - c >= 0 ? "+" : ""}{o - c}</div>
    </div>
  );
}

/** Incidents opened vs. closed per day: is the queue growing or shrinking? */
export function OpenedClosedChart({ trend, height = 200 }: { trend: { days: string[]; opened: number[]; closed: number[] }; height?: number }) {
  const rows = trend.days.map((d, i) => ({ day: d, opened: trend.opened[i] ?? 0, closed: trend.closed[i] ?? 0 }));
  const opened = rows.reduce((s, r) => s + r.opened, 0);
  const closed = rows.reduce((s, r) => s + r.closed, 0);
  if (!rows.length || (!opened && !closed)) return <Empty title="No incidents opened or closed in this window" />;
  return (
    <div>
      <div style={{ height }} className="-ml-2">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: 0 }} barCategoryGap="22%" barGap={2}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="day" tickFormatter={dayLabel} tick={{ fill: "var(--axis)", fontSize: 11 }} axisLine={{ stroke: "var(--grid)" }} tickLine={false} minTickGap={24} />
            <YAxis allowDecimals={false} tick={{ fill: "var(--axis)", fontSize: 11 }} axisLine={false} tickLine={false} width={32} />
            <Tooltip content={<Tip />} cursor={{ fill: "rgb(var(--ink) / 0.05)" }} />
            <Bar dataKey="opened" fill={OPENED} radius={[3, 3, 0, 0]} isAnimationActive={false} />
            <Bar dataKey="closed" fill={CLOSED} radius={[3, 3, 0, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink2">
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: OPENED }} /> Opened <span className="tnum text-muted">{opened.toLocaleString("en-US")}</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: CLOSED }} /> Closed <span className="tnum text-muted">{closed.toLocaleString("en-US")}</span>
        </span>
        <span className="text-muted">
          net {opened - closed >= 0 ? "+" : ""}
          {(opened - closed).toLocaleString("en-US")} in {rows.length} days
        </span>
      </div>
    </div>
  );
}
