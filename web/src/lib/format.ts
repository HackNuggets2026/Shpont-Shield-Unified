// Number, money and time formatting. Many AI costs are fractions of a cent, so USD precision adapts.

export function usd(v: number | null | undefined, opts: { compact?: boolean } = {}): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a === 0) return "$0";
  if (opts.compact && a >= 1000) return money(v);
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
  if (a >= 1000) return (v < 0 ? "-$" : "$") + countTick(a);
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

// ---- enterprise-scale formatting -------------------------------------------------------------
// Numbers on the console are read at a glance: three significant digits, compact suffixes.

function sig3(a: number): string {
  // 4.12, 41.2, 412 — trailing zeros after the point dropped.
  const d = a >= 100 ? 0 : a >= 10 ? 1 : 2;
  return a.toFixed(d).replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");
}

function compactParts(a: number): [number, string] {
  if (a >= 1e12) return [a / 1e12, "T"];
  if (a >= 1e9) return [a / 1e9, "B"];
  if (a >= 1e6) return [a / 1e6, "M"];
  return [a / 1e3, "k"];
}

const NEXT_SUFFIX: Record<string, string> = { k: "M", M: "B", B: "T", T: "T" };

/** |a| >= 1000 as three significant digits plus suffix; 999.96k rolls over to 1M. */
function compactNum(a: number): string {
  const [n, suf] = compactParts(a);
  const s = sig3(n);
  if (Number(s) >= 1000 && suf !== "T") return sig3(n / 1000) + NEXT_SUFFIX[suf];
  return s + suf;
}

/** Compact currency: $412k, $41.2k, $1.21M. Below $1,000 falls back to `usd` (cents and fractions). */
export function money(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a < 1000) return usd(v);
  return (v < 0 ? "-$" : "$") + compactNum(a);
}

/** Money per unit, whole dollars once past $10: "$106", "$4.20", "$0.031". */
export function unitMoney(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 10000) return money(v);
  if (a >= 10) return "$" + Math.round(v).toLocaleString("en-US");
  return usd(v);
}

/** Compact counts: 5,012 stays exact, 18.3k, 1.83M. */
export function count(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a < 10000) return Math.round(v).toLocaleString("en-US");
  return (v < 0 ? "-" : "") + compactNum(a);
}

/**
 * Percent with precision where it matters: 42%, 7.5%, 98.7%, 99.93% (so 99.96% never reads as 100%).
 * Ratios are 0..1.
 */
export function pctAuto(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const p = v * 100;
  const a = Math.abs(p);
  if (a === 0) return "0%";
  if (a < 0.1) return p < 0 ? ">-0.1%" : "<0.1%";
  if (a >= 99.995 && a < 100) return "99.99%";
  if (a >= 99 && a < 100) return p.toFixed(2) + "%";
  if (a >= 90 && a < 100) return p.toFixed(1) + "%";
  if (a >= 10) return p.toFixed(0) + "%";
  return p.toFixed(1) + "%";
}

export type DeltaDir = "up" | "down" | "flat" | "new";

/** Relative change cur vs prev, ready to print: "▲ 8.5%", "▼ 3.1%", "– 0%", "new". */
export function delta(cur: number | null | undefined, prev: number | null | undefined): { dir: DeltaDir; ratio: number | null; text: string } {
  if (cur === null || cur === undefined || prev === null || prev === undefined) return { dir: "flat", ratio: null, text: "—" };
  if (!prev) return cur ? { dir: "new", ratio: null, text: "new" } : { dir: "flat", ratio: 0, text: "– 0%" };
  const r = (cur - prev) / Math.abs(prev);
  if (Math.abs(r) < 0.0005) return { dir: "flat", ratio: r, text: "– 0%" };
  const a = Math.abs(r) * 100;
  const body = a >= 1000 ? times(r + 1) : a >= 100 ? `${a.toFixed(0)}%` : a >= 10 ? `${a.toFixed(0)}%` : `${a.toFixed(1)}%`;
  return { dir: r > 0 ? "up" : "down", ratio: r, text: `${r > 0 ? "▲" : "▼"} ${body}` };
}

/** Change in percentage points, for rates such as adherence: "▲ 0.4 pp". */
export function deltaPp(cur: number | null | undefined, prev: number | null | undefined): { dir: DeltaDir; text: string } {
  if (cur === null || cur === undefined || prev === null || prev === undefined) return { dir: "flat", text: "—" };
  const d = (cur - prev) * 100;
  if (Math.abs(d) < 0.05) return { dir: "flat", text: "– 0 pp" };
  return { dir: d > 0 ? "up" : "down", text: `${d > 0 ? "▲" : "▼"} ${Math.abs(d).toFixed(Math.abs(d) >= 10 ? 0 : 1)} pp` };
}

/** "3.4×" for ratios such as spend vs team median. */
export function times(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return (v >= 100 ? Math.round(v).toString() : v >= 10 ? v.toFixed(0) : v.toFixed(1)) + "×";
}

/** Axis ticks for counts: 0, 500, 1.2k, 18k, 1.8M. */
export function countTick(v: number): string {
  const a = Math.abs(v);
  if (a < 1000) return String(Math.round(v * 100) / 100);
  const [n, suf] = compactParts(a);
  return (v < 0 ? "-" : "") + (n >= 10 ? Math.round(n) : Math.round(n * 10) / 10) + suf;
}
