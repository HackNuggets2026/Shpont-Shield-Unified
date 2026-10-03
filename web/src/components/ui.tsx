import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Link } from "react-router-dom";
import type { UseQueryResult } from "@tanstack/react-query";
import { ApiError } from "../api";
import { IconAlert } from "./icons";

export function cx(...c: (string | false | null | undefined)[]): string {
  return c.filter(Boolean).join(" ");
}

// ---- layout -----------------------------------------------------------------------------------

export function PageHeader({
  title,
  subtitle,
  actions,
  back,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  back?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        {back && <div className="mb-1.5 text-xs text-muted">{back}</div>}
        <h1 className="truncate text-2xl font-semibold tracking-tight text-ink">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-ink2">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({
  title,
  subtitle,
  actions,
  children,
  className,
  bodyClass,
  flush,
  tint,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClass?: string;
  flush?: boolean;
  /** A CSS color washed softly over the top of the card. */
  tint?: string;
}) {
  return (
    <section className={cx("soft-card relative min-w-0 overflow-hidden rounded-2xl", className)}>
      {tint && <Wash color={tint} />}
      {(title || actions) && (
        <header className={cx("relative flex flex-wrap items-start justify-between gap-2 px-5 pt-4", flush ? "border-b border-line/60 pb-3" : "pb-1")}>
          <div className="min-w-0">
            {title && <h2 className="text-[15px] font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cx("relative", flush ? "" : "px-5 pb-5 pt-3", !flush && !(title || actions) && "pt-5", bodyClass)}>{children}</div>
    </section>
  );
}

/** A soft color wash fading down from the top edge of a card. */
export function Wash({ color, height = "6rem", opacity = 0.1 }: { color: string; height?: string; opacity?: number }) {
  return <span aria-hidden className="pointer-events-none absolute inset-x-0 top-0" style={{ height, background: `linear-gradient(to bottom, ${color}, transparent)`, opacity }} />;
}

export const TONE_COLOR: Record<Tone, string> = {
  neutral: "rgb(var(--muted))",
  good: "rgb(var(--good))",
  warn: "rgb(var(--warn))",
  serious: "rgb(var(--serious))",
  bad: "rgb(var(--bad))",
  info: "rgb(var(--info))",
  accent: "rgb(var(--accent))",
  cc: "rgb(var(--cc))",
};

/** Horizontal scroll wrapper so wide tables never break the page width. */
export function TableWrap({ children, maxH }: { children: ReactNode; maxH?: string }) {
  return (
    <div className={cx("overflow-x-auto", maxH && "overflow-y-auto")} style={maxH ? { maxHeight: maxH } : undefined}>
      {children}
    </div>
  );
}

export type Tone = "neutral" | "good" | "warn" | "serious" | "bad" | "info" | "accent" | "cc";

const TONE: Record<Tone, string> = {
  neutral: "bg-ink/[0.06] text-ink2 ring-ink/10",
  good: "bg-good/10 text-good ring-good/25",
  warn: "bg-warn/10 text-warn ring-warn/30",
  serious: "bg-serious/10 text-serious ring-serious/30",
  bad: "bg-bad/10 text-bad ring-bad/30",
  info: "bg-info/10 text-info ring-info/25",
  accent: "bg-accent/10 text-accent ring-accent/25",
  cc: "bg-cc/10 text-cc ring-cc/30",
};

const DOT: Record<Tone, string> = {
  neutral: "bg-muted",
  good: "bg-good",
  warn: "bg-warn",
  serious: "bg-serious",
  bad: "bg-bad",
  info: "bg-info",
  accent: "bg-accent",
  cc: "bg-cc",
};

export function Pill({ tone = "neutral", children, dot, className, title }: { tone?: Tone; children: ReactNode; dot?: boolean; className?: string; title?: string }) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex max-w-full items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        TONE[tone],
        className,
      )}
    >
      {dot && <span className={cx("h-1.5 w-1.5 shrink-0 rounded-full", DOT[tone])} />}
      <span className="truncate">{children}</span>
    </span>
  );
}

export function Dot({ tone }: { tone: Tone }) {
  return <span className={cx("inline-block h-2 w-2 shrink-0 rounded-full", DOT[tone])} />;
}

// ---- controls ---------------------------------------------------------------------------------

type BtnVariant = "primary" | "secondary" | "ghost" | "danger" | "good";
const BTN: Record<BtnVariant, string> = {
  primary: "bg-brand text-white shadow-sm hover:brightness-110",
  secondary: "bg-panel text-ink ring-1 ring-inset ring-line hover:bg-raised",
  ghost: "text-ink2 hover:bg-raised hover:text-ink",
  danger: "bg-bad text-white hover:bg-bad/90 shadow-sm",
  good: "bg-good text-white hover:bg-good/90 shadow-sm",
};

export function Button({
  variant = "secondary",
  size = "md",
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant; size?: "sm" | "md" }) {
  return (
    <button
      type="button"
      className={cx(
        "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-sm",
        BTN[variant],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  size = "sm",
}: {
  value: T;
  options: { value: T; label: ReactNode }[];
  onChange: (v: T) => void;
  size?: "sm" | "md";
}) {
  return (
    <div className="inline-flex rounded-full bg-raised p-0.5 ring-1 ring-inset ring-line/70" role="tablist">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={o.value === value}
          onClick={() => onChange(o.value)}
          className={cx(
            "rounded-full px-3 font-medium transition-colors",
            size === "sm" ? "h-6 text-xs" : "h-8 text-sm",
            o.value === value ? "bg-ink text-page shadow-sm" : "text-muted hover:text-ink",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Select({
  value,
  onChange,
  options,
  label,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  label: string;
}) {
  return (
    <label className="inline-flex items-center gap-1.5 text-xs text-muted">
      <span className="sr-only sm:not-sr-only">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-7 rounded-md border border-line bg-panel px-2 text-xs text-ink focus:border-accent focus:outline-none"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function Toggle({ checked, onChange, disabled, label }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50",
        checked ? "bg-good" : "bg-ink/20",
      )}
    >
      <span className={cx("inline-block h-4 w-4 rounded-full bg-white shadow transition-transform", checked ? "translate-x-[18px]" : "translate-x-0.5")} />
    </button>
  );
}

// ---- data display -----------------------------------------------------------------------------

export function Kpi({
  label,
  value,
  sub,
  tone,
  icon,
  to,
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  tone?: Tone;
  icon?: ReactNode;
  to?: string;
}) {
  const body = (
    <div className={cx("soft-card relative h-full overflow-hidden rounded-2xl p-4", to && "transition hover:-translate-y-0.5 hover:shadow-lg")}>
      {tone && tone !== "neutral" && <Wash color={TONE_COLOR[tone]} opacity={0.08} />}
      <div className="relative flex items-center justify-between gap-2 text-xs font-medium text-muted">
        <span className="min-w-0 leading-tight">{label}</span>
        {icon && <span className={cx("shrink-0", tone && tone !== "neutral" ? TONE[tone].split(" ")[1] : "text-muted")}>{icon}</span>}
      </div>
      <div className="relative mt-2 text-2xl font-semibold tracking-tight text-ink">{value}</div>
      {sub && <div className="relative mt-1 text-xs text-muted">{sub}</div>}
    </div>
  );
  return to ? (
    <Link to={to} className="block focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 rounded-2xl">
      {body}
    </Link>
  ) : (
    body
  );
}

/** A thin progress meter; turns warn/bad past thresholds. */
export function Meter({ value, max, tone }: { value: number; max: number | null | undefined; tone?: Tone }) {
  const ratio = max ? Math.min(value / max, 1) : 0;
  const t: Tone = tone ?? (ratio >= 0.9 ? "bad" : ratio >= 0.7 ? "warn" : "accent");
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink/10" role="meter" aria-valuenow={value} aria-valuemax={max ?? undefined}>
      <div className={cx("h-full rounded-full transition-[width]", DOT[t])} style={{ width: `${Math.max(ratio * 100, value > 0 ? 1.5 : 0)}%` }} />
    </div>
  );
}

export function Empty({ title, hint, icon }: { title: ReactNode; hint?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 px-4 py-10 text-center">
      {icon && <div className="mb-1 text-muted">{icon}</div>}
      <div className="text-sm font-medium text-ink2">{title}</div>
      {hint && <div className="max-w-sm text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function ErrorBox({ error, retry, compact }: { error: unknown; retry?: () => void; compact?: boolean }) {
  const msg = error instanceof Error ? error.message : String(error);
  const status = error instanceof ApiError && error.status ? ` (${error.status})` : "";
  return (
    <div className={cx("flex items-start gap-2 rounded-lg bg-bad/10 text-sm text-bad ring-1 ring-inset ring-bad/25", compact ? "px-3 py-2" : "px-4 py-3")} role="alert">
      <IconAlert className="mt-0.5 shrink-0" />
      <div className="min-w-0 flex-1 break-words">
        {msg}
        {status}
      </div>
      {retry && (
        <button type="button" onClick={retry} className="shrink-0 text-xs font-medium underline underline-offset-2">
          Retry
        </button>
      )}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("animate-pulse rounded-md bg-ink/[0.07]", className)} />;
}

export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2 p-1" aria-busy="true">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-5" />
      ))}
    </div>
  );
}

/** Renders loading / error / data for a query. */
export function Q<T>({
  q,
  children,
  rows,
}: {
  q: UseQueryResult<T>;
  children: (data: T) => ReactNode;
  rows?: number;
}) {
  if (q.isPending) return <Loading rows={rows} />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  return <>{children(q.data)}</>;
}

export function Field({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-ink2">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-muted">{hint}</span>}
    </label>
  );
}

export function Stat({ label, value }: { label: ReactNode; value: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] font-medium uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-0.5 truncate text-sm font-medium text-ink">{value}</div>
    </div>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cx("font-mono text-[12px]", className)}>{children}</span>;
}

/** A short list of mono chips; the rest collapse into "+N" with the full list on hover. */
export function Chips({ items, max = 3, empty = "any" }: { items: string[]; max?: number; empty?: string }) {
  if (!items.length) return <span className="text-xs text-muted">{empty}</span>;
  const shown = items.slice(0, max);
  const rest = items.length - shown.length;
  return (
    <span className="inline-flex flex-wrap gap-1" title={items.join(", ")}>
      {shown.map((t) => (
        <span key={t} className="whitespace-nowrap rounded bg-ink/[0.06] px-1.5 py-px font-mono text-[11px] text-ink2">
          {t}
        </span>
      ))}
      {rest > 0 && <span className="rounded px-1 py-px text-[11px] text-muted">+{rest}</span>}
    </span>
  );
}
