import { useMemo } from "react";
import { countC } from "../../lib/compact";
import { ruleLabel, RULES } from "../../lib/security";
import { Empty, cx } from "../ui";
import { IconTerminal } from "../icons";

export interface Cell {
  rule: string;
  department: string;
  open: number;
  total: number;
}

/**
 * Incidents by rule (rows) × department (columns). One hue, light to dark, so the hot spots read at a glance in
 * either theme; every non-zero cell prints its count. Clicking a cell filters the incidents table to it.
 */
export function RuleDeptHeatmap({
  cells,
  mode,
  selected,
  onPick,
}: {
  cells: Cell[];
  mode: "open" | "total";
  selected?: { rule: string; department: string };
  onPick: (rule: string, department: string) => void;
}) {
  const { rules, depts, at, max, rowSum, colSum } = useMemo(() => {
    const at = new Map<string, number>();
    const rowSum = new Map<string, number>();
    const colSum = new Map<string, number>();
    for (const c of cells) {
      const v = mode === "open" ? c.open : c.total;
      at.set(`${c.rule}|${c.department}`, (at.get(`${c.rule}|${c.department}`) ?? 0) + v);
      rowSum.set(c.rule, (rowSum.get(c.rule) ?? 0) + v);
      colSum.set(c.department, (colSum.get(c.department) ?? 0) + v);
    }
    // Rows and columns with the most incidents first; empty ones drop out in "open" mode.
    const rules = [...rowSum].filter(([, v]) => v > 0 || mode === "total").sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([k]) => k);
    const depts = [...colSum].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([k]) => k);
    return { rules, depts, at, max: Math.max(1, ...at.values()), rowSum, colSum };
  }, [cells, mode]);

  if (!rules.length || !depts.length)
    return <Empty title={mode === "open" ? "No open incidents anywhere" : "No incidents in 30 days"} hint="Cells appear as detections raise incidents." />;

  const shade = (v: number) => (v > 0 ? 0.1 + 0.75 * Math.sqrt(v / max) : 0);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] border-separate border-spacing-[2px] text-xs">
        <thead>
          <tr>
            <th className="w-[170px] px-2 pb-1 text-left text-[11px] font-medium text-muted">Rule</th>
            {depts.map((d) => (
              <th key={d} className="px-1 pb-1 text-center align-bottom text-[11px] font-medium text-ink2" title={d}>
                <span className="line-clamp-2 leading-tight">{d}</span>
              </th>
            ))}
            <th className="px-2 pb-1 text-right text-[11px] font-medium text-muted">All</th>
          </tr>
        </thead>
        <tbody>
          {rules.map((r) => (
            <tr key={r}>
              <th scope="row" className="max-w-[170px] px-2 text-left font-medium text-ink" title={RULES[r]?.what}>
                <span className="flex items-center gap-1.5">
                  {RULES[r]?.cc && <IconTerminal size={11} className="shrink-0 text-cc" />}
                  <span className="truncate">{ruleLabel(r)}</span>
                </span>
              </th>
              {depts.map((d) => {
                const v = at.get(`${r}|${d}`) ?? 0;
                const a = shade(v);
                const sel = selected?.rule === r && selected.department === d;
                return (
                  <td key={d} className="p-0">
                    <button
                      type="button"
                      disabled={!v}
                      onClick={() => onPick(r, d)}
                      title={`${ruleLabel(r)} · ${d}: ${v.toLocaleString("en-US")} ${mode === "open" ? "open" : "in 30 days"}${v ? " (click to list them)" : ""}`}
                      className={cx(
                        "tnum flex h-8 w-full min-w-[52px] items-center justify-center rounded-[4px] font-medium transition-shadow",
                        v ? "cursor-pointer hover:ring-2 hover:ring-ink/40" : "cursor-default bg-ink/[0.03] text-transparent",
                        sel && "ring-2 ring-accent",
                        a > 0.5 ? "text-white" : "text-ink",
                      )}
                      style={v ? { background: `rgb(var(--bad) / ${a.toFixed(3)})` } : undefined}
                    >
                      {v ? countC(v) : "·"}
                    </button>
                  </td>
                );
              })}
              <td className="tnum px-2 text-right font-medium text-ink2">{countC(rowSum.get(r) ?? 0)}</td>
            </tr>
          ))}
          <tr>
            <th scope="row" className="px-2 pt-1 text-left text-[11px] font-medium text-muted">
              All rules
            </th>
            {depts.map((d) => (
              <td key={d} className="tnum px-1 pt-1 text-center font-medium text-ink2">
                {countC(colSum.get(d) ?? 0)}
              </td>
            ))}
            <td className="tnum px-2 pt-1 text-right font-semibold text-ink">{countC([...colSum.values()].reduce((s, v) => s + v, 0))}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
