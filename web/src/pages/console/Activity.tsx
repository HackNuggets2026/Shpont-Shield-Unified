import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { admin, audit, downloadExport, type AuditFilters, type AuditStats, type ShieldEvent } from "../../api";
import { org } from "../../orgApi";
import { ActivityFeed } from "../../components/ActivityFeed";
import { BarList } from "../../components/charts";
import { DecisionPill } from "../../components/pills";
import { Button, Card, Empty, ErrorBox, Loading, Mono, PageHeader, Pill, Segmented, Select, TableWrap, Toggle, cx } from "../../components/ui";
import { IconDownload, IconPause, IconPlay, IconX } from "../../components/icons";
import { count, dateTime, pct, timeOfDay } from "../../lib/format";

// Decisions, weakest first, in the console's status tokens.
const ACTIONS = ["allow", "log", "warn", "redact", "block"] as const;
const ACTION_COLOR: Record<string, string> = {
  allow: "rgb(var(--good))",
  log: "rgb(var(--muted))",
  warn: "rgb(var(--warn))",
  redact: "rgb(var(--serious))",
  block: "rgb(var(--bad))",
};
const RANGES = [
  { value: "900", label: "15 min" },
  { value: "3600", label: "1 hour" },
  { value: "21600", label: "6 hours" },
  { value: "86400", label: "24 hours" },
  { value: "604800", label: "7 days" },
  { value: "", label: "All time" },
];
const FILTER_KEYS = ["action", "control", "principal", "channel", "direction", "q", "window"] as const;
type FilterKey = (typeof FILTER_KEYS)[number];
const PAGE = 200;
const MAX_ROWS = 1000;

/** Filters live in the query string, so other pages can link here with ?control=x or ?principal=y. */
function useFilters() {
  const [sp, setSp] = useSearchParams();
  const filters = useMemo(() => {
    const f: AuditFilters = {};
    for (const k of FILTER_KEYS) {
      const v = sp.get(k);
      if (v) (f as Record<string, string | number>)[k] = k === "window" ? Number(v) : v;
    }
    return f;
  }, [sp]);
  const set = useCallback(
    (patch: Partial<Record<FilterKey | "view", string>>) =>
      setSp(
        (prev) => {
          const n = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(patch)) {
            if (v) n.set(k, v);
            else n.delete(k);
          }
          return n;
        },
        { replace: true },
      ),
    [setSp],
  );
  return { filters, set, view: sp.get("view") === "feed" ? "feed" : "decisions", clear: () => setSp(new URLSearchParams(), { replace: true }) } as const;
}

/** Audit trail and live tail in one: every gateway decision, filterable, exportable, streaming in every 2 s. */
export function ActivityPage() {
  const { filters, set, view, clear } = useFilters();
  return (
    <div>
      <PageHeader
        title="Activity"
        subtitle={
          view === "decisions"
            ? "Every decision the gateway made, live. Filter, open a row for its findings and timings, or export what you see."
            : "Grants, incidents, leases, meter reports and zombie flags across the organization, newest first."
        }
        actions={
          <>
            {view === "decisions" && <ExportButtons filters={filters} />}
            <Segmented
              value={view}
              onChange={(v) => set({ view: v === "feed" ? "feed" : "" })}
              options={[
                { value: "decisions", label: "Decisions" },
                { value: "feed", label: "Org feed" },
              ]}
            />
          </>
        }
      />
      {view === "decisions" ? <Decisions filters={filters} set={set} clear={clear} /> : <OrgFeed />}
    </div>
  );
}

function ExportButtons({ filters }: { filters: AuditFilters }) {
  const [err, setErr] = useState<string | null>(null);
  const run = async (fmt: string) => {
    setErr(null);
    try {
      await downloadExport(audit.exportPath(fmt, filters), `audit-${fmt}.${fmt === "csv" ? "csv" : "jsonl"}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <div className="flex items-center gap-1.5">
      {err && <span className="max-w-[220px] truncate text-xs text-bad" title={err}>{err}</span>}
      {(["jsonl", "csv", "ocsf", "ecs"] as const).map((f) => (
        <Button key={f} size="sm" onClick={() => run(f)} title={`Download ${f.toUpperCase()} with the current filters${f === "ocsf" || f === "ecs" ? " (SIEM stream)" : ""}`}>
          <IconDownload size={13} />
          {f.toUpperCase()}
        </Button>
      ))}
    </div>
  );
}

function OrgFeed() {
  const [mode, setMode] = useState<"interesting" | "all">("interesting");
  return (
    <Card
      flush
      title={mode === "interesting" ? "Needs a look" : "Everything"}
      actions={
        <Segmented
          value={mode}
          onChange={setMode}
          options={[
            { value: "interesting", label: "Needs a look" },
            { value: "all", label: "Everything" },
          ]}
        />
      }
    >
      {mode === "interesting" ? (
        <ActivityFeed
          key="interesting"
          load={(p) => org.activity({ ...p, interesting: true })}
          queryKey={["admin", "activity", "interesting"]}
          linkPeople
          maxH="calc(100vh - 260px)"
          interval={5000}
        />
      ) : (
        <ActivityFeed key="all" load={admin.activity} queryKey={["admin", "activity"]} linkPeople maxH="calc(100vh - 260px)" />
      )}
    </Card>
  );
}

function Decisions({ filters, set, clear }: { filters: AuditFilters; set: (p: Partial<Record<FilterKey, string>>) => void; clear: () => void }) {
  const key = JSON.stringify(filters);
  const [live, setLive] = useState(true);
  const [paused, setPaused] = useState(false);
  const [rows, setRows] = useState<ShieldEvent[]>([]);
  const [pending, setPending] = useState<ShieldEvent[]>([]);
  const [conn, setConn] = useState<"ok" | "error" | "idle">("idle");
  const [open, setOpen] = useState<ShieldEvent | null>(null);

  const first = useQuery({ queryKey: ["audit", "events", key], queryFn: () => audit.events({ ...filters, limit: PAGE }) });
  useEffect(() => {
    if (first.data) {
      setRows(first.data);
      setPending([]);
    }
  }, [first.data]);

  // About 48 buckets over the chosen range (the last 24 hours when the range is "all time").
  const statsWindow = filters.window ?? 86400;
  const stats = useQuery({
    queryKey: ["audit", "stats", key],
    queryFn: () => audit.stats({ ...filters, window: statsWindow, bucket: Math.max(1, Math.round(statsWindow / 48)) }),
    refetchInterval: live ? 10_000 : false,
  });

  // Live tail: every 2 s, anything newer than the newest row held (shown or waiting behind Pause).
  const cursor = useRef(0);
  cursor.current = Math.max(rows[0]?.ts ?? 0, pending[0]?.ts ?? 0);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const filtersRef = useRef(filters);
  filtersRef.current = filters;
  const loaded = !!first.data;
  useEffect(() => {
    if (!live || !loaded) {
      setConn("idle");
      return;
    }
    let stop = false;
    const tick = async () => {
      try {
        const fresh = await audit.events({ ...filtersRef.current, limit: PAGE, since: cursor.current || undefined });
        if (stop) return;
        setConn("ok");
        if (!fresh.length) return;
        if (pausedRef.current) setPending((p) => [...fresh, ...p].slice(0, MAX_ROWS));
        else setRows((r) => [...fresh, ...r].slice(0, MAX_ROWS));
      } catch {
        if (!stop) setConn("error");
      }
    };
    const id = window.setInterval(tick, 2000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, [live, loaded, key]);

  const flush = () => {
    setRows((r) => [...pending, ...r].slice(0, MAX_ROWS));
    setPending([]);
  };

  const channels = useMemo(() => [...new Set(rows.map((e) => e.channel).filter(Boolean))].sort(), [rows]);
  const controls = useMemo(
    () => [...new Set([...Object.keys(stats.data?.controls ?? {}), ...rows.flatMap((e) => e.findings.map((f) => f.control))])].sort(),
    [stats.data, rows],
  );
  const anyFilter = Object.keys(filters).length > 0;

  return (
    <>
      <StatsBand q={stats} onReason={(r) => set({ q: r })} onCell={(control, action) => set({ control, action })} />
      <FilterBar filters={filters} set={set} clear={anyFilter ? clear : undefined} channels={channels} controls={controls} />
      <Card
        flush
        title={
          <span className="flex items-center gap-2">
            Decisions
            <span className="text-xs font-normal text-muted">{count(rows.length)} shown</span>
          </span>
        }
        actions={
          <div className="flex items-center gap-3 text-xs text-ink2">
            {pending.length > 0 && (
              <button
                type="button"
                onClick={flush}
                className="rounded-full bg-accent/10 px-2.5 py-0.5 font-medium text-accent ring-1 ring-inset ring-accent/25 hover:bg-accent/20"
              >
                {count(pending.length)} new
              </button>
            )}
            {live && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => (paused ? (flush(), setPaused(false)) : setPaused(true))}
                title={paused ? "Resume and show what arrived" : "Pause: new rows wait behind a badge"}
              >
                {paused ? <IconPlay size={13} /> : <IconPause size={13} />}
                {paused ? "Resume" : "Pause"}
              </Button>
            )}
            <span
              className="flex items-center gap-1.5"
              title={conn === "ok" ? "Connected, polling every 2 s" : conn === "error" ? "Cannot reach the gateway" : "Live is off"}
            >
              <span className={cx("h-2 w-2 rounded-full", conn === "ok" ? "bg-good" : conn === "error" ? "bg-bad" : "bg-muted", conn === "ok" && !paused && "animate-pulse")} />
              Live
            </span>
            <Toggle
              checked={live}
              onChange={(v) => {
                setLive(v);
                if (!v) {
                  flush();
                  setPaused(false);
                }
              }}
              label="Live updates"
            />
          </div>
        }
      >
        {first.isLoading ? (
          <div className="p-5">
            <Loading rows={6} />
          </div>
        ) : first.error ? (
          <div className="p-5">
            <ErrorBox error={first.error} retry={() => first.refetch()} />
          </div>
        ) : rows.length === 0 ? (
          <Empty
            title={anyFilter ? "No decisions match these filters" : "No decisions yet"}
            hint={anyFilter ? "Widen the time range or clear a filter." : "Send a request through the gateway (or the Playground) and it shows up here within two seconds."}
          />
        ) : (
          <EventTable rows={rows} onOpen={setOpen} />
        )}
      </Card>
      {open && <DetailSheet e={open} onClose={() => setOpen(null)} />}
    </>
  );
}

// ---- stats band -------------------------------------------------------------------------------

function StatsBand({
  q,
  onReason,
  onCell,
}: {
  q: { data?: AuditStats; error: unknown };
  onReason: (r: string) => void;
  onCell: (control: string, action: string) => void;
}) {
  const s = q.data;
  const total = s?.latency_ms.total;
  return (
    <Card className="mb-4" tint="rgb(var(--accent))">
      {q.error ? (
        <ErrorBox error={q.error} compact />
      ) : !s ? (
        <Loading rows={3} />
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-baseline gap-x-5 gap-y-1 text-xs text-ink2">
            <span>
              <span className="tnum text-sm font-semibold text-ink">{count(s.total)}</span> decisions in the last {rangeLabel(s.window_s)}
            </span>
            <span>
              blocked <span className="tnum font-medium text-bad">{s.blocked_rate == null ? "n/a" : pct(s.blocked_rate)}</span>
            </span>
            <span>
              redacted <span className="tnum font-medium text-serious">{s.redacted_rate == null ? "n/a" : pct(s.redacted_rate)}</span>
            </span>
            {total && (
              <span>
                latency p50 <span className="tnum font-medium text-ink">{ms(total.p50)}</span>, p95 <span className="tnum font-medium text-ink">{ms(total.p95)}</span>
              </span>
            )}
          </div>
          <div className="grid gap-6 lg:grid-cols-[1.6fr_1fr_1fr]">
            <MiniBlock title="Decisions over time">
              <Timeline s={s} />
            </MiniBlock>
            <MiniBlock title="Top reasons">
              <BarList
                rows={s.top_reasons.slice(0, 5).map((r) => ({
                  key: r.reason,
                  value: r.count,
                  label: (
                    <button type="button" className="truncate text-left hover:text-accent" title={`Search for "${r.reason}"`} onClick={() => onReason(r.reason)}>
                      {r.reason}
                    </button>
                  ),
                }))}
                fmt={count}
                tone="rgb(var(--bad))"
                empty="Nothing blocked or flagged"
              />
            </MiniBlock>
            <MiniBlock title="Control x action">
              <Heatmap controls={s.controls} onPick={onCell} />
            </MiniBlock>
          </div>
        </>
      )}
    </Card>
  );
}

function MiniBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted">{title}</div>
      {children}
    </div>
  );
}

function Timeline({ s }: { s: AuditStats }) {
  if (!s.total) return <Empty title="No decisions in this range" />;
  const short = s.window_s <= 86400;
  const tick = (t: number) => (short ? timeOfDay(t).slice(0, 5) : new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" }));
  return (
    <div>
      <div className="-ml-2 h-[140px]">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={s.timeline} margin={{ top: 4, right: 4, bottom: 0, left: 0 }} barCategoryGap="12%">
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="t" tickFormatter={tick} tick={{ fill: "var(--axis)", fontSize: 10 }} axisLine={{ stroke: "var(--grid)" }} tickLine={false} minTickGap={28} />
            <YAxis allowDecimals={false} tick={{ fill: "var(--axis)", fontSize: 10 }} axisLine={false} tickLine={false} width={32} />
            <Tooltip
              cursor={{ fill: "rgb(var(--ink) / 0.05)" }}
              content={({ active, payload, label }) =>
                active && payload?.length ? (
                  <div className="rounded-lg border border-line bg-panel px-3 py-2 text-xs shadow-xl">
                    <div className="mb-1 font-medium text-ink">{dateTime(Number(label))}</div>
                    {[...payload]
                      .filter((p) => Number(p.value) > 0)
                      .reverse()
                      .map((p) => (
                        <div key={String(p.dataKey)} className="flex justify-between gap-4 text-ink2">
                          <span className="flex items-center gap-1.5">
                            <span className="h-2 w-2 rounded-sm" style={{ background: p.color }} />
                            {String(p.dataKey)}
                          </span>
                          <span className="tnum text-ink">{p.value}</span>
                        </div>
                      ))}
                  </div>
                ) : null
              }
            />
            {ACTIONS.map((a) => (
              <Bar key={a} dataKey={a} stackId="1" fill={ACTION_COLOR[a]} isAnimationActive={false} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-ink2">
        {ACTIONS.filter((a) => s.by_action[a]).map((a) => (
          <span key={a} className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-sm" style={{ background: ACTION_COLOR[a] }} />
            {a} <span className="tnum text-muted">{count(s.by_action[a])}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

/** Findings per control (rows) and the action each took (columns); a cell filters the table to it. */
function Heatmap({ controls, onPick }: { controls: AuditStats["controls"]; onPick: (control: string, action: string) => void }) {
  const names = Object.entries(controls)
    .map(([c, by]) => [c, Object.values(by).reduce((a, b) => a + b, 0)] as const)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 7)
    .map(([c]) => c);
  if (!names.length) return <Empty title="No findings in this range" />;
  const max = Math.max(1, ...names.flatMap((c) => Object.values(controls[c])));
  return (
    <table className="w-full table-fixed border-separate border-spacing-[2px] text-[11px]">
      <thead>
        <tr>
          <th className="w-[34%]" />
          {ACTIONS.map((a) => (
            <th key={a} className="truncate pb-0.5 text-center font-medium text-muted">
              {a}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {names.map((c) => (
          <tr key={c}>
            <th scope="row" className="truncate pr-1 text-left font-medium text-ink2" title={c}>
              {c}
            </th>
            {ACTIONS.map((a) => {
              const v = controls[c][a] ?? 0;
              return (
                <td key={a} className="p-0">
                  <button
                    type="button"
                    disabled={!v}
                    onClick={() => onPick(c, a)}
                    title={`${c}: ${v} ${a}`}
                    className="tnum relative h-5 w-full overflow-hidden rounded text-center text-ink ring-1 ring-inset ring-line/50 enabled:hover:ring-accent"
                  >
                    <span aria-hidden className="absolute inset-0" style={{ background: ACTION_COLOR[a], opacity: v ? 0.12 + 0.6 * Math.sqrt(v / max) : 0 }} />
                    <span className="relative">{v || ""}</span>
                  </button>
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---- filters ----------------------------------------------------------------------------------

function FilterBar({
  filters,
  set,
  clear,
  channels,
  controls,
}: {
  filters: AuditFilters;
  set: (p: Partial<Record<FilterKey, string>>) => void;
  clear?: () => void;
  channels: string[];
  controls: string[];
}) {
  const opts = (xs: string[], cur?: string) => [{ value: "", label: "Any" }, ...[...new Set([...xs, ...(cur ? [cur] : [])])].map((x) => ({ value: x, label: x }))];
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2.5">
      <Select label="Decision" value={filters.action ?? ""} onChange={(v) => set({ action: v })} options={opts([...ACTIONS], filters.action)} />
      <Select label="Control" value={filters.control ?? ""} onChange={(v) => set({ control: v })} options={opts(controls, filters.control)} />
      <Select label="Channel" value={filters.channel ?? ""} onChange={(v) => set({ channel: v })} options={opts(channels, filters.channel)} />
      <Select label="Direction" value={filters.direction ?? ""} onChange={(v) => set({ direction: v })} options={opts(["input", "output"], filters.direction)} />
      <Select label="Range" value={filters.window ? String(filters.window) : ""} onChange={(v) => set({ window: v })} options={RANGES} />
      <TextFilter placeholder="Person" value={filters.principal ?? ""} onCommit={(v) => set({ principal: v })} width="w-36" />
      <TextFilter placeholder="Search reason, tool, finding" value={filters.q ?? ""} onCommit={(v) => set({ q: v })} width="w-56" />
      {clear && (
        <Button size="sm" variant="ghost" onClick={clear}>
          <IconX size={12} />
          Clear
        </Button>
      )}
    </div>
  );
}

/** Commits on Enter or blur, so typing does not refetch on every key. */
function TextFilter({ value, onCommit, placeholder, width }: { value: string; onCommit: (v: string) => void; placeholder: string; width: string }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  const commit = () => {
    if (v.trim() !== value) onCommit(v.trim());
  };
  return (
    <input
      value={v}
      onChange={(e) => setV(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && commit()}
      placeholder={placeholder}
      aria-label={placeholder}
      className={cx("h-7 rounded-md border border-line bg-panel px-2 text-xs text-ink placeholder:text-muted focus:border-accent focus:outline-none", width)}
    />
  );
}

// ---- table and detail -------------------------------------------------------------------------

const findingTone = (f: { action: string; shadow?: boolean }) =>
  f.shadow ? "neutral" : f.action === "block" ? "bad" : f.action === "redact" ? "serious" : f.action === "warn" ? "warn" : "info";

function EventTable({ rows, onOpen }: { rows: ShieldEvent[]; onOpen: (e: ShieldEvent) => void }) {
  return (
    <TableWrap maxH="calc(100vh - 260px)">
      <table className="w-full min-w-[980px] text-left text-xs">
        <thead className="sticky top-0 z-10 bg-panel text-[11px] text-muted">
          <tr className="border-b border-line/60">
            <th className="px-4 py-2 font-medium">Time</th>
            <th className="px-2 py-2 font-medium">Decision</th>
            <th className="px-2 py-2 font-medium">Channel / tool</th>
            <th className="px-2 py-2 font-medium">Who</th>
            <th className="px-2 py-2 font-medium">Reason</th>
            <th className="px-2 py-2 font-medium">Findings</th>
            <th className="px-2 py-2 font-medium">Text (masked)</th>
            <th className="px-4 py-2 text-right font-medium">Latency</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={`${e.request_id}-${e.direction}-${e.ts}`} onClick={() => onOpen(e)} className="cursor-pointer border-b border-line/40 align-top hover:bg-raised/60">
              <td className="tnum whitespace-nowrap px-4 py-2 text-ink2" title={dateTime(e.ts)}>
                {timeOfDay(e.ts)}
              </td>
              <td className="px-2 py-2">
                <DecisionPill decision={e.action} />
              </td>
              <td className="max-w-[160px] px-2 py-2 text-ink2">
                <div className="truncate">
                  {e.channel}
                  <span className="text-muted"> · {e.direction}</span>
                </div>
                {(e.tool || e.model) && <div className="truncate text-muted">{e.tool ?? e.model}</div>}
              </td>
              <td className="max-w-[140px] px-2 py-2">
                <Link
                  to={`/console/people/${encodeURIComponent(e.principal)}`}
                  onClick={(ev) => ev.stopPropagation()}
                  className="block truncate font-medium text-ink hover:text-accent"
                >
                  {e.principal}
                </Link>
                {e.owner && e.owner !== e.principal && <div className="truncate text-muted">for {e.owner}</div>}
              </td>
              <td className="max-w-[220px] truncate px-2 py-2 text-ink2" title={e.reason}>
                {e.reason || <span className="text-muted">-</span>}
              </td>
              <td className="max-w-[220px] px-2 py-2">
                <div className="flex flex-wrap gap-1">
                  {e.findings.slice(0, 3).map((f, i) => (
                    <Pill key={i} tone={findingTone(f)} title={f.detail}>
                      {f.control}/{f.category}
                    </Pill>
                  ))}
                  {e.findings.length > 3 && <span className="text-muted">+{e.findings.length - 3}</span>}
                </div>
              </td>
              <td className="max-w-[260px] truncate px-2 py-2 font-mono text-[11px] text-ink2" title={e.text ?? ""}>
                {e.text ?? <span className="font-sans text-muted">{e.action === "block" ? "withheld" : "not stored"}</span>}
              </td>
              <td className="tnum whitespace-nowrap px-4 py-2 text-right text-ink2">{ms(e.latency_ms?.total)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

function DetailSheet({ e, onClose }: { e: ShieldEvent; onClose: () => void }) {
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => ev.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const stages = Object.entries(e.latency_ms ?? {}).filter(([k]) => k !== "total");
  const maxStage = Math.max(1, ...stages.map(([, v]) => v));
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Decision details">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <aside className="relative flex h-full w-full max-w-lg flex-col overflow-hidden border-l border-line bg-panel shadow-2xl">
        <header className="flex items-start justify-between gap-2 border-b border-line px-5 py-3.5">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <DecisionPill decision={e.action} />
              <span className="text-sm font-semibold text-ink">
                {e.channel} · {e.direction}
              </span>
            </div>
            <div className="mt-1 text-xs text-muted">{dateTime(e.ts)}</div>
          </div>
          <button type="button" onClick={onClose} className="rounded p-1 text-muted hover:bg-raised hover:text-ink" aria-label="Close">
            <IconX />
          </button>
        </header>
        <div className="space-y-5 overflow-y-auto px-5 py-4 text-xs">
          <dl className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-1.5">
            <Row k="Who">
              <Link to={`/console/people/${encodeURIComponent(e.principal)}`} className="font-medium text-accent hover:underline">
                {e.principal}
              </Link>
              {e.owner && e.owner !== e.principal && <span className="text-muted"> (agent of {e.owner})</span>}
            </Row>
            <Row k="Team / role">{[e.team, e.role].filter(Boolean).join(" / ") || "-"}</Row>
            <Row k="Model / tool">{[e.model, e.tool].filter(Boolean).join(" / ") || "-"}</Row>
            <Row k="Reason">{e.reason || "-"}</Row>
            <Row k="Status">{e.status_code}</Row>
            <Row k="Policy version">
              <Mono>{e.policy_version ?? "-"}</Mono>
            </Row>
            <Row k="Request id">
              <Mono>{e.request_id}</Mono>
            </Row>
            <Row k="Text sha256">
              <Mono className="break-all">{e.text_sha256 ?? "-"}</Mono>
            </Row>
            {e.src_ip && <Row k="Source IP">{e.src_ip}</Row>}
          </dl>

          <section>
            <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted">Findings ({e.findings.length})</h3>
            {e.findings.length === 0 ? (
              <div className="text-muted">No control flagged this.</div>
            ) : (
              <ul className="space-y-2">
                {e.findings.map((f, i) => (
                  <li key={i} className="rounded-lg border border-line/70 p-2.5">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-medium text-ink">{f.control}</span>
                      <span className="text-muted">/ {f.category}</span>
                      <DecisionPill decision={f.action} />
                      {f.tier && <Pill>tier {f.tier}</Pill>}
                      {f.shadow && <Pill tone="info">shadow</Pill>}
                      {f.score != null && <span className="tnum ml-auto text-muted">score {f.score}</span>}
                    </div>
                    {f.detail && <div className="mt-1 break-words text-ink2">{f.detail}</div>}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section>
            <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted">
              Stages{e.latency_ms?.total != null && <span className="normal-case"> (total {ms(e.latency_ms.total)})</span>}
            </h3>
            {stages.length === 0 ? (
              <div className="text-muted">No stage timings.</div>
            ) : (
              <ul className="space-y-1.5">
                {stages.map(([k, v]) => (
                  <li key={k} className="grid grid-cols-[110px_1fr_60px] items-center gap-2">
                    <span className="truncate text-ink2">{k}</span>
                    <span className="h-1.5 overflow-hidden rounded-full bg-ink/[0.07]">
                      <span className="block h-full rounded-full bg-accent" style={{ width: `${(v / maxStage) * 100}%` }} />
                    </span>
                    <span className="tnum text-right text-ink">{ms(v)}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section>
            <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted">Text, masked</h3>
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-raised p-2.5 font-mono text-[11px] text-ink2">
              {e.text ?? (e.action === "block" ? "Withheld: blocked text is never stored." : "Not stored for this decision.")}
            </pre>
          </section>
        </div>
      </aside>
    </div>
  );
}

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted">{k}</dt>
      <dd className="min-w-0 break-words text-ink">{children}</dd>
    </>
  );
}

function ms(v: number | null | undefined): string {
  if (v == null) return "-";
  return v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${v < 10 ? v.toFixed(1) : Math.round(v)} ms`;
}

function rangeLabel(s: number): string {
  return RANGES.find((r) => r.value === String(s))?.label ?? `${Math.round(s / 60)} min`;
}
