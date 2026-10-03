// Number, money and time formatting. Many AI costs are fractions of a cent, so USD precision adapts.

export function usd(v: number | null | undefined, opts: { compact?: boolean } = {}): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a === 0) return "$0";
  if (opts.compact && a >= 1000) {
    return "$" + new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v);
  }
  if (a >= 100) return "$" + v.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (a >= 1) return "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (a >= 0.01) return "$" + v.toFixed(3).replace(/0$/, "");
  if (a >= 0.0001) return "$" + v.toFixed(4);
  if (a >= 0.000001) return "$" + v.toPrecision(2);
  return "<$0.000001";
}

/** Axis ticks: short and never scientific. */
export function usdTick(v: number): string {
  const a = Math.abs(v);
  if (a === 0) return "$0";
  if (a >= 1000) return "$" + new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v);
  if (a >= 1) return "$" + (Number.isInteger(v) ? v : v.toFixed(1));
  if (a >= 0.01) return "$" + v.toFixed(2);
  if (a >= 0.001) return "$" + v.toFixed(3);
  return "$" + v.toPrecision(1);
}

export function tokens(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(a >= 1e10 ? 0 : 1) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(a >= 1e7 ? 0 : 1) + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(a >= 1e4 ? 0 : 1) + "k";
  return String(Math.round(v));
}

export function num(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString("en-US", { maximumFractionDigits: digits });
}

export function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const p = v * 100;
  return (p === 100 || p === 0 ? p.toFixed(0) : p.toFixed(digits)) + "%";
}

export function minutes(m: number | null | undefined): string {
  if (m === null || m === undefined) return "—";
  if (m < 1) return `${Math.round(m * 60)}s`;
  if (m < 60) return `${m.toFixed(m < 10 ? 1 : 0)} min`;
  const h = Math.floor(m / 60);
  const r = Math.round(m % 60);
  return r ? `${h}h ${r}m` : `${h}h`;
}

/** "3m ago", "in 12m". ts is unix seconds. */
export function ago(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return "—";
  const d = now - ts;
  const a = Math.abs(d);
  let s: string;
  if (a < 10) return d >= 0 ? "just now" : "in a moment";
  if (a < 60) s = `${Math.round(a)}s`;
  else if (a < 3600) s = `${Math.round(a / 60)}m`;
  else if (a < 86400) s = `${Math.round(a / 3600)}h`;
  else if (a < 86400 * 30) s = `${Math.round(a / 86400)}d`;
  else return dateShort(ts);
  return d >= 0 ? `${s} ago` : `in ${s}`;
}

export function dateTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function timeOfDay(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function dateShort(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** "2026-09-14" -> "Sep 14" */
export function dayLabel(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
}

/** Seconds left until ts, as "1h 04m" / "12m 30s" / "expired". */
export function countdown(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return "no expiry";
  const s = Math.floor(ts - now);
  if (s <= 0) return "expired";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(sec).padStart(2, "0")}s`;
}

export function titleCase(s: string): string {
  return s.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
