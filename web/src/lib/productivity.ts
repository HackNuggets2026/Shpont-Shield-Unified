import { useQuery } from "@tanstack/react-query";
import { admin, type ActivityEvent } from "../api";

const DAY = 86400;

/** Sums detail.value over every event of a kind in the window (the API has no aggregate for metric values). */
async function sumMetric(kind: string, since: number, decision?: string): Promise<number> {
  let before: number | undefined;
  let total = 0;
  for (let page = 0; page < 20; page++) {
    const rows: ActivityEvent[] = await admin.activity({ kind, limit: 500, before });
    for (const e of rows) {
      if (e.ts < since) return total;
      if (decision && e.decision !== decision) continue;
      const v = (e.detail as { value?: number } | null)?.value;
      if (typeof v === "number") total += v;
    }
    if (rows.length < 500) break;
    before = rows[rows.length - 1].ts;
  }
  return total;
}

export interface CcProductivity {
  usd: number;
  commits: number;
  prs: number;
  linesAdded: number;
  costPerCommit: number | null;
  costPerPr: number | null;
  linesPerUsd: number | null;
}

/** Claude Code spend set against the work it produced (commits, PRs, lines) over `days`. */
export function useCcProductivity(days = 30) {
  return useQuery({
    queryKey: ["admin", "cc-productivity", days],
    staleTime: 60_000,
    refetchInterval: 60_000,
    queryFn: async (): Promise<CcProductivity> => {
      const since = Date.now() / 1000 - days * DAY;
      const [ts, commits, prs, linesAdded] = await Promise.all([
        admin.timeseries("source", days),
        sumMetric("metric.commit", since),
        sumMetric("metric.pull_request", since),
        sumMetric("metric.lines_of_code", since, "added"),
      ]);
      const usd = (ts.series["claude_code"] ?? []).reduce((a, b) => a + b, 0);
      return {
        usd,
        commits,
        prs,
        linesAdded,
        costPerCommit: commits ? usd / commits : null,
        costPerPr: prs ? usd / prs : null,
        linesPerUsd: usd ? linesAdded / usd : null,
      };
    },
  });
}

export interface WorkOutput {
  commits: number;
  prs: number;
  linesAdded: number;
}

async function eventsSince(kind: string, since: number): Promise<ActivityEvent[]> {
  const out: ActivityEvent[] = [];
  let before: number | undefined;
  for (let page = 0; page < 20; page++) {
    const rows = await admin.activity({ kind, limit: 500, before });
    for (const e of rows) {
      if (e.ts < since) return out;
      out.push(e);
    }
    if (rows.length < 500) break;
    before = rows[rows.length - 1].ts;
  }
  return out;
}

/** Commits, PRs and lines Claude Code reported per workflow label, over `days`. */
export function useOutputByWorkflow(days = 30) {
  return useQuery({
    queryKey: ["admin", "output-by-workflow", days],
    staleTime: 60_000,
    refetchInterval: 60_000,
    queryFn: async (): Promise<Record<string, WorkOutput>> => {
      const since = Date.now() / 1000 - days * DAY;
      const [commits, prs, lines] = await Promise.all([
        eventsSince("metric.commit", since),
        eventsSince("metric.pull_request", since),
        eventsSince("metric.lines_of_code", since),
      ]);
      const out: Record<string, WorkOutput> = {};
      const row = (wf: string | null) => (out[wf ?? "(none)"] ??= { commits: 0, prs: 0, linesAdded: 0 });
      const val = (e: ActivityEvent) => Number((e.detail as { value?: number } | null)?.value ?? 0);
      for (const e of commits) row(e.workflow).commits += val(e);
      for (const e of prs) row(e.workflow).prs += val(e);
      for (const e of lines) if (e.decision === "added") row(e.workflow).linesAdded += val(e);
      return out;
    },
  });
}
