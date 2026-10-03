import type { ReactNode } from "react";
import { countC, usdC } from "../../lib/compact";
import { Empty, cx } from "../ui";

export interface RollupRow {
  key: string;
  label?: ReactNode;
  cells: { value: number; fmt?: "usd" | "count"; tone?: "warn" | "bad" | "cc" }[];
}

/**
 * A compact aggregate table (by resource, by department, ...): the first column names the group, the rest are
 * right-aligned numbers. A row is a button that filters the list below; the active one is highlighted.
 */
export function RollupTable({
  title,
  head,
  rows,
  active,
  onPick,
  empty = "Nothing yet",
  max = 8,
}: {
  title: string;
  head: string[];
  rows: RollupRow[];
  active?: string;
  onPick?: (key: string) => void;
  empty?: string;
  max?: number;
}) {
  const shown = rows.slice(0, max);
  const rest = rows.length - shown.length;
  return (
    <div className="min-w-0">
      <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">{title}</div>
      {rows.length === 0 ? (
        <div className="rounded-lg border border-dashed border-line">
          <Empty title={empty} />
        </div>
      ) : (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-[11px] text-muted">
              <th className="pb-1 text-left font-medium">{head[0]}</th>
              {head.slice(1).map((h) => (
                <th key={h} className="pb-1 pl-2 text-right font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr
                key={r.key}
                onClick={onPick ? () => onPick(active === r.key ? "" : r.key) : undefined}
                className={cx("border-t border-line/60", onPick && "cursor-pointer hover:bg-raised/60", active === r.key && "bg-accent/[0.07]")}
                title={onPick ? (active === r.key ? "Clear this filter" : "Filter the list to this") : undefined}
              >
                <td className="max-w-[180px] truncate py-1.5 pr-2 font-medium text-ink">{r.label ?? r.key}</td>
                {r.cells.map((c, i) => (
                  <td
                    key={i}
                    className={cx(
                      "tnum py-1.5 pl-2 text-right",
                      c.value && c.tone === "warn" ? "font-semibold text-warn" : c.value && c.tone === "bad" ? "font-semibold text-bad" : c.value && c.tone === "cc" ? "text-cc" : "text-ink2",
                      !c.value && "text-muted",
                    )}
                  >
                    {c.fmt === "usd" ? usdC(c.value) : countC(c.value)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {rest > 0 && <div className="mt-1 text-[11px] text-muted">+{rest} more; use the filter to see them</div>}
    </div>
  );
}
