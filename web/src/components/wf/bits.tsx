// Small pieces of the workflow-first console: the workflow badge, the "who uses it" bar,
// the workflow tag on incidents and the soft card the pages are built from.
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { IconBolt, IconBug, IconChart, IconChat, IconFlow, IconPhone, IconPullRequest } from "../icons";
import { cx } from "../ui";
import { wfLabel, wfMeta } from "../../lib/workflows";
import { money, pctAuto } from "../../lib/format";

export const wfPath = (name: string) => `/console/workflows/${encodeURIComponent(name)}`;

export function WfGlyph({ id, size = 18 }: { id: string; size?: number }) {
  const icon = wfMeta(id).icon;
  const p = { size };
  if (icon === "bug") return <IconBug {...p} />;
  if (icon === "pr") return <IconPullRequest {...p} />;
  if (icon === "phone") return <IconPhone {...p} />;
  if (icon === "chart") return <IconChart {...p} />;
  if (icon === "chat") return <IconChat {...p} />;
  if (icon === "bolt") return <IconBolt {...p} />;
  return <IconFlow {...p} />;
}

/** The workflow's color as a soft rounded badge with its glyph. */
export function WfBadge({ id, color, size = "md" }: { id: string; color: string; size?: "sm" | "md" | "lg" }) {
  const box = size === "lg" ? "h-12 w-12 rounded-2xl" : size === "sm" ? "h-6 w-6 rounded-lg" : "h-10 w-10 rounded-xl";
  const glyph = size === "lg" ? 24 : size === "sm" ? 13 : 20;
  return (
    <span className={cx("relative inline-flex shrink-0 items-center justify-center overflow-hidden", box)} style={{ color }}>
      <span className="absolute inset-0" style={{ background: color, opacity: 0.16 }} />
      <span className="relative">
        <WfGlyph id={id} size={glyph} />
      </span>
    </span>
  );
}

/** A workflow named on another object (an incident, a lease): dot + label, dashed when inferred. */
export function WfTagChip({ id, color, likely, link = true }: { id: string | null; color?: string; likely?: boolean; link?: boolean }) {
  if (!id)
    return (
      <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-dashed border-line px-2 py-0.5 text-[11px] text-muted" title="Happened outside any workflow on the menu">
        outside the menu
      </span>
    );
  const body = (
    <span
      className={cx(
        "inline-flex max-w-full items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium text-ink2",
        likely ? "border border-dashed border-ink/25" : "bg-ink/[0.05] ring-1 ring-inset ring-ink/10",
      )}
      title={likely ? "Inferred from the person's usual work, not from the evidence" : "From the evidence events"}
    >
      <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: color ?? "var(--s-other)" }} />
      <span className="truncate">{wfLabel(id)}</span>
      {likely && <span className="font-normal text-muted">likely</span>}
    </span>
  );
  return link ? (
    <Link to={wfPath(id)} onClick={(e) => e.stopPropagation()} className="hover:opacity-80">
      {body}
    </Link>
  ) : (
    body
  );
}

/** A 100% bar of who orders this kind of work; departments keep their fixed colors everywhere. */
export function DeptBar({ rows: all, colors, top = 2 }: { rows: { department: string; usd: number }[]; colors: Record<string, string>; top?: number }) {
  const total = all.reduce((a, r) => a + r.usd, 0);
  if (!total) return <div className="text-[11px] text-muted">Nobody yet</div>;
  return (
    <div title={all.map((r) => `${r.department}: ${money(r.usd)} (${pctAuto(r.usd / total)})`).join("\n")}>
      <div className="flex h-2 w-full gap-[2px] overflow-hidden rounded-full">
        {all
          .filter((r) => r.usd / total >= 0.004)
          .map((r) => (
            <span key={r.department} className="h-full first:rounded-l-full last:rounded-r-full" style={{ width: `${(r.usd / total) * 100}%`, background: colors[r.department] ?? "var(--s-other)" }} />
          ))}
      </div>
      <div className="mt-1.5 flex min-w-0 flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted">
        {all.slice(0, top).map((r) => (
          <span key={r.department} className="inline-flex min-w-0 items-center gap-1">
            <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: colors[r.department] ?? "var(--s-other)" }} />
            <span className="truncate text-ink2">{r.department}</span>
            <span className="tnum">{pctAuto(r.usd / total)}</span>
          </span>
        ))}
        {all.length > top && <span>+{all.length - top} more</span>}
      </div>
    </div>
  );
}

/** Soft rounded card used across the workflow-first pages. */
export function Panel({
  title,
  hint,
  actions,
  children,
  className,
  flush,
}: {
  title?: ReactNode;
  hint?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  flush?: boolean;
}) {
  return (
    <section className={cx("min-w-0 rounded-2xl soft-card", className)}>
      {(title || actions) && (
        <header className={cx("flex flex-wrap items-start justify-between gap-2 px-5 pt-4", flush && "pb-3")}>
          <div className="min-w-0">
            {title && <h2 className="text-[15px] font-semibold text-ink">{title}</h2>}
            {hint && <p className="mt-0.5 text-xs text-muted">{hint}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={flush ? "" : "px-5 pb-5 pt-3"}>{children}</div>
    </section>
  );
}

export function DeptLegend({ colors }: { colors: Record<string, string> }) {
  const entries = Object.entries(colors).filter(([k]) => k !== "(none)" && k !== "Unassigned");
  if (!entries.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
      {entries.map(([k, c]) => (
        <span key={k} className="inline-flex items-center gap-1">
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: c }} />
          {k}
        </span>
      ))}
    </div>
  );
}
