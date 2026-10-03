// What one run costs, as a price scale: half of runs land left of "typical", one in ten past "expensive".
import { unitMoney } from "../../lib/format";
import { count } from "../../lib/format";
import { Empty } from "../ui";

interface Mark {
  v: number;
  label: string;
  above: boolean;
  dashed?: boolean;
}

export function CostPerRun({
  p50,
  p90,
  mean,
  cap,
  runs,
  color,
}: {
  p50: number | null;
  p90: number | null;
  mean: number | null;
  cap: number | null;
  runs: number;
  color: string;
}) {
  if (!runs || p50 === null || p90 === null) return <Empty title="No measured runs yet" hint="Prices appear after the first labelled runs." />;
  // A cap far above real prices would squash the scale; it then gets a note instead of a mark.
  const capOnScale = cap !== null && cap <= Math.max(p90, mean ?? 0) * 2.5;
  const top = Math.max(p90, capOnScale ? (cap ?? 0) : 0, mean ?? 0) * 1.18 || 1;
  const x = (v: number) => `${Math.min((v / top) * 100, 100)}%`;
  const raw: Omit<Mark, "above">[] = [
    { v: p50, label: `Typical ${unitMoney(p50)}` },
    { v: p90, label: `Expensive ${unitMoney(p90)}` },
  ];
  if (mean !== null) raw.push({ v: mean, label: `Average ${unitMoney(mean)}` });
  if (cap !== null && capOnScale) raw.push({ v: cap, label: `Cap ${unitMoney(cap)}`, dashed: true });
  // Labels go to the first free lane (above, below, then further out) so close prices never overlap.
  const LANES = ["-top-6", "top-6", "-top-11", "top-11"];
  const lastInLane: number[] = LANES.map(() => -1);
  const marks: (Mark & { lane: string })[] = raw
    .sort((a, b) => a.v - b.v)
    .map((m) => {
      const pos = m.v / top;
      let li = lastInLane.findIndex((last) => last < 0 || pos - last > 0.17);
      if (li < 0) li = 0;
      lastInLane[li] = pos;
      return { ...m, above: li % 2 === 0, lane: LANES[li] };
    });
  const tall = marks.some((m) => m.lane.endsWith("11"));
  const skew = mean !== null && mean > p50 * 1.6;

  return (
    <div>
      <div className={`relative mx-2 mb-1 ${tall ? "mt-12 pb-12" : "mt-7 pb-7"}`}>
        <div className="relative h-4 overflow-hidden rounded-full bg-ink/[0.06]">
          <div className="absolute inset-y-0 left-0 rounded-l-full" style={{ width: x(p50), background: color, opacity: 0.75 }} />
          <div className="absolute inset-y-0" style={{ left: x(p50), width: `calc(${x(p90)} - ${x(p50)})`, background: color, opacity: 0.35 }} />
          <div className="absolute inset-y-0" style={{ left: x(p90), right: 0, background: color, opacity: 0.12 }} />
        </div>
        {marks.map((m) => (
          <div key={m.label} className="absolute top-0" style={{ left: x(m.v) }}>
            <div className={m.dashed ? "absolute -top-1 h-6 border-l-2 border-dashed border-ink/50" : "absolute -top-1 h-6 w-[2px] bg-ink/70"} />
            <div
              className={`tnum absolute whitespace-nowrap text-[11px] font-medium text-ink ${m.lane} ${
                m.v / top < 0.08 ? "-translate-x-1" : m.v / top > 0.9 ? "-translate-x-full" : "-translate-x-1/2"
              }`}
            >
              {m.label}
            </div>
          </div>
        ))}
      </div>
      {cap !== null && !capOnScale && <div className="mx-2 -mt-3 mb-2 text-right text-[11px] text-muted">Per-run cap {unitMoney(cap)}, far above any real run →</div>}
      <div className="mt-2 grid gap-3 text-xs text-ink2 sm:grid-cols-3">
        <p>
          <b className="font-semibold text-ink">Half of runs</b> cost under {unitMoney(p50)}.
        </p>
        <p>
          <b className="font-semibold text-ink">1 run in 10</b> costs more than {unitMoney(p90)}
          {cap !== null ? (p90 > cap ? `, above the ${unitMoney(cap)} cap` : `, still under the ${unitMoney(cap)} cap`) : ""}.
        </p>
        <p>
          {skew ? (
            <>
              The <b className="font-semibold text-ink">average</b> ({unitMoney(mean)}) sits far above typical: a few long runs drive the bill.
            </>
          ) : (
            <>
              Runs are fairly even; {count(runs)} measured.
            </>
          )}
        </p>
      </div>
    </div>
  );
}
