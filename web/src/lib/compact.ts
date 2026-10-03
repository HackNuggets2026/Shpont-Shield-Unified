// Enterprise-scale number formatting for the operational pages: $412k, 1.8M, 12.4k.
// Local until the shared compact formatters land in lib/format.ts.
import { usd } from "./format";

const compactFmt = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

/** Money: exact below $1,000 ($412, $3.20), compact above ($41.2k, $1.8M). */
export function usdC(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (Math.abs(v) < 1000) return usd(v);
  return "$" + compactFmt.format(v).replace("K", "k");
}

/** Counts: exact below 10,000 (9,812), compact above (12.4k, 1.8M). */
export function countC(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (Math.abs(v) < 10_000) return Math.round(v).toLocaleString("en-US");
  return compactFmt.format(v).replace("K", "k");
}

/** Full number for tooltips next to a compact one. */
export const exact = (v: number | null | undefined, money = false): string =>
  v === null || v === undefined ? "" : money ? "$" + v.toLocaleString("en-US", { maximumFractionDigits: 2 }) : v.toLocaleString("en-US");

/** Age of a timestamp in words that scale: "12 min", "5 h", "3 days". */
export function age(ts: number, now = Date.now() / 1000): string {
  const s = Math.max(0, now - ts);
  if (s < 60) return "<1 min";
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 86400 * 2) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} days`;
}
