import { cx } from "../ui";
import { DEFAULT_THRESHOLDS } from "../../lib/security";

type Th = { watch: number; restricted: number };

const fill = (score: number, t: Th) => (score >= t.restricted ? "bg-bad" : score >= t.watch ? "bg-warn" : "bg-good/70");

/** A risk score on a scale with the watch and restricted thresholds marked. */
export function RiskMeter({ score, thresholds, compact }: { score: number; thresholds?: Th | null; compact?: boolean }) {
  const t = thresholds ?? DEFAULT_THRESHOLDS;
  const max = Math.max(t.restricted * 1.25, score * 1.05, 1);
  const at = (v: number) => `${Math.min(v / max, 1) * 100}%`;
  const marks = [
    ["watch", "Watch"],
    ["restricted", "Restricted"],
  ] as const;
  return (
    <div className={cx("min-w-0", compact ? "w-28" : "w-full")}>
      <div className="flex items-baseline justify-between gap-2">
        <span className={cx("tnum font-semibold text-ink", compact ? "text-sm" : "text-2xl tracking-tight")}>{Math.round(score)}</span>
        {!compact && <span className="text-[11px] text-muted">restricted at {t.restricted}</span>}
      </div>
      <div className={cx("relative mt-1 overflow-visible rounded-full bg-ink/10", compact ? "h-1.5" : "h-2")}>
        <div className={cx("h-full rounded-full transition-[width]", fill(score, t))} style={{ width: at(score) }} />
        {marks.map(([k, label]) => (
          <span key={k} className="absolute -top-0.5 h-[calc(100%+4px)] w-px bg-ink/40" style={{ left: at(t[k]) }} title={`${label} at ${t[k]}`} />
        ))}
      </div>
      {!compact && (
        <div className="relative mt-1 h-3 text-[10px] text-muted">
          {marks.map(([k, label]) => (
            <span key={k} className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: at(t[k]) }}>
              {label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
