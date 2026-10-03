// The home screen's building blocks: one slim summary strip and a large tile per workflow.
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { admin } from "../../api";
import { Sparkline } from "../charts";
import { Delta } from "../org";
import { IconAlert, IconChevronRight } from "../icons";
import { Skeleton, cx } from "../ui";
import { count, money, pctAuto, unitMoney } from "../../lib/format";
import { resourceWord, wfLabel, wfMeta } from "../../lib/workflows";
import type { WfRow } from "../../lib/wfData";
import { DeptBar, WfBadge, wfPath } from "./bits";

function StripItem({ label, children, to, tone }: { label: string; children: ReactNode; to?: string; tone?: "bad" }) {
  const body = (
    <div className={cx("h-full min-w-0 rounded-xl px-4 py-3 transition-colors", to && "hover:bg-raised/70", tone === "bad" && "bg-bad/[0.06]")}>
      <div className="text-[11px] font-medium uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-1">{children}</div>
    </div>
  );
  return to ? (
    <Link to={to} className="block min-w-0 rounded-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50">
      {body}
    </Link>
  ) : (
    body
  );
}

/** Three numbers above the menu: what we spend, how well people follow policy, what is on fire. */
export function SummaryStrip({ total, prev, adherence, checks }: { total: number; prev: number; adherence: number | null | undefined; checks: number }) {
  const ov = useQuery({ queryKey: ["admin", "overview"], queryFn: admin.overview, refetchInterval: 10_000 });
  const open = useQuery({ queryKey: ["admin", "incidents", "open"], queryFn: () => admin.incidents("open"), refetchInterval: 10_000 });
  const high = (open.data?.incidents ?? []).filter((i) => i.severity === "high");
  const month = new Date().toLocaleDateString("en-US", { month: "long", timeZone: "UTC" });
  const nudges = adherence !== null && adherence !== undefined && adherence < 1 ? Math.round(1 / (1 - adherence)) : null;
  return (
    <div className="grid gap-1 rounded-2xl soft-card p-1.5 sm:grid-cols-3">
      <StripItem label={`Spend in ${month}`}>
        {ov.data ? (
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-2xl font-semibold tracking-tight text-ink">{money(ov.data.spend.month_to_date)}</span>
            <span className="text-sm text-ink2">
              so far · forecast <b className="font-semibold text-ink">{money(ov.data.spend.month_forecast)}</b>
            </span>
          </div>
        ) : (
          <Skeleton className="h-8 w-48" />
        )}
        <div className="mt-0.5 text-xs text-muted">
          {money(total)} in the last 30 days <Delta cur={total} prev={prev} className="ml-1 text-[11px]" />
        </div>
      </StripItem>
      <StripItem label="Policy adherence">
        <span className={cx("text-2xl font-semibold tracking-tight", adherence !== null && adherence !== undefined && adherence < 0.95 ? "text-warn" : "text-ink")}>
          {pctAuto(adherence)}
        </span>
        <div className="mt-0.5 text-xs text-muted">
          of {count(checks)} checks passed untouched{nudges ? ` · 1 in ${nudges} needed a nudge` : ""}
        </div>
      </StripItem>
      <StripItem label="High-severity incidents" to="/console/security" tone={high.length ? "bad" : undefined}>
        <span className={cx("inline-flex items-center gap-2 text-2xl font-semibold tracking-tight", high.length ? "text-bad" : "text-ink")}>
          {high.length > 0 && <IconAlert size={20} />}
          {open.isPending ? "…" : high.length}
          <span className="text-sm font-normal text-ink2">open</span>
        </span>
        <div className="mt-0.5 truncate text-xs text-muted">
          {high.length ? `${high.map((i) => i.principal).slice(0, 3).join(", ")} · ` : "Nothing urgent · "}
          {open.data ? `${open.data.incidents.length - high.length} lower-severity open` : ""}
        </div>
      </StripItem>
    </div>
  );
}

function PriceCell({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="min-w-0" title={hint}>
      <div className="text-[11px] text-muted">{label}</div>
      <div className="tnum truncate text-sm font-semibold text-ink">{value}</div>
    </div>
  );
}

/** A workflow as a menu item: price, volume, trend, adherence and who orders it. */
export function WorkflowTile({ r, deptColors, openIncidents }: { r: WfRow; deptColors: Record<string, string>; openIncidents?: number }) {
  const meta = wfMeta(r.name, r.wf?.description);
  const leased = r.running.reduce<Record<string, number>>((m, l) => ({ ...m, [l.resource]: (m[l.resource] ?? 0) + 1 }), {});
  const zombies = r.running.filter((l) => l.flags.length).length;
  const adhTone = r.adherence === null ? "text-muted" : r.adherence < 0.98 ? "text-warn" : "text-good";
  return (
    <Link
      to={wfPath(r.name)}
      className={cx(
        "group relative flex h-full flex-col overflow-hidden rounded-2xl soft-card p-5 transition hover:-translate-y-0.5 hover:shadow-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
        r.wf && !r.wf.enabled && "opacity-70",
      )}
    >
      <span className="pointer-events-none absolute inset-x-0 top-0 h-24" style={{ background: `linear-gradient(to bottom, ${r.color}, transparent)`, opacity: 0.09 }} />
      <div className="relative flex items-start gap-3">
        <WfBadge id={r.name} color={r.color} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <h3 className="truncate text-base font-semibold text-ink">{wfLabel(r.name)}</h3>
            {r.wf?.approval === "admin" && <span className="rounded-full bg-cc/10 px-1.5 py-px text-[10.5px] font-medium text-cc">needs approval</span>}
            {r.wf && !r.wf.enabled && <span className="rounded-full bg-ink/10 px-1.5 py-px text-[10.5px] font-medium text-ink2">off</span>}
          </div>
          <p className="mt-0.5 line-clamp-2 text-xs leading-relaxed text-muted">{meta.pitch}</p>
        </div>
        <IconChevronRight size={16} className="mt-1 shrink-0 text-muted transition-transform group-hover:translate-x-0.5" />
      </div>

      <div className="relative mt-4 flex items-end justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[28px] font-semibold leading-none tracking-tight text-ink">{money(r.usd)}</div>
          <div className="mt-1.5 text-xs text-muted">last 30 days</div>
          <div className="mt-0.5 flex items-center gap-1 whitespace-nowrap text-[11px] text-muted" title="Last 14 days vs the 14 before">
            <Delta cur={r.trend.recent} prev={r.trend.earlier} className="text-[11px]" /> over 2 weeks
          </div>
        </div>
        <div className="w-[42%] max-w-[170px]">
          <Sparkline values={r.spark} color={r.color} height={40} min={0} />
        </div>
      </div>

      <div className="relative mt-4 grid grid-cols-3 gap-2 rounded-xl bg-raised/70 px-3 py-2.5">
        <PriceCell label="Runs" value={count(r.runs)} />
        <PriceCell label="Typical run" value={unitMoney(r.p50)} hint="Median cost of one run (half of runs cost less)" />
        <PriceCell label="Expensive run" value={unitMoney(r.p90)} hint="1 run in 10 costs more than this (p90)" />
      </div>

      <div className="relative mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <span className="inline-flex items-center gap-1.5" title={`${count(r.checks)} policy checks · ${count(r.blocked)} blocked · ${count(r.redacted)} redacted · ${count(r.warned)} warned`}>
          <span className={cx("font-semibold tnum", adhTone)}>{pctAuto(r.adherence)}</span>
          <span className="text-muted">adherence</span>
        </span>
        {Object.entries(leased).map(([res, n]) => (
          <span key={res} className="inline-flex items-center gap-1 text-ink2">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-good opacity-40" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-good" />
            </span>
            {n} {resourceWord(res).toLowerCase()} running
          </span>
        ))}
        {zombies > 0 && <span className="text-warn">{zombies} idle</span>}
        {!!openIncidents && (
          <span className="inline-flex items-center gap-1 font-medium text-bad">
            <IconAlert size={12} /> {openIncidents} open incident{openIncidents === 1 ? "" : "s"}
          </span>
        )}
      </div>

      <div className="relative mt-auto pt-4">
        <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">Who uses it</div>
        <DeptBar rows={r.depts} colors={deptColors} />
      </div>
    </Link>
  );
}

export function TileSkeleton() {
  return (
    <div className="rounded-2xl border border-line/70 bg-panel p-5">
      <div className="flex gap-3">
        <Skeleton className="h-10 w-10 rounded-xl" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-3 w-full" />
        </div>
      </div>
      <Skeleton className="mt-5 h-8 w-28" />
      <Skeleton className="mt-4 h-12 w-full rounded-xl" />
      <Skeleton className="mt-6 h-2 w-full" />
    </div>
  );
}
