import { useQuery } from "@tanstack/react-query";
import { me, type ActivityEvent } from "../../api";

const DAY = 86400;
const PAGE = 500;
const MAX_PAGES = 12;

/**
 * Every event under my key in the last `days`, paged back with `before` (the API returns at most 500 per call
 * and has no source/kind filter). Capped at MAX_PAGES so a very busy key cannot stall the page.
 */
export function useMyEvents(days = 30) {
  return useQuery({
    queryKey: ["me", "events-window", days],
    staleTime: 20_000,
    refetchInterval: 30_000,
    queryFn: async (): Promise<{ events: ActivityEvent[]; since: number; complete: boolean }> => {
      const since = Date.now() / 1000 - days * DAY;
      const out: ActivityEvent[] = [];
      let before: number | undefined;
      for (let i = 0; i < MAX_PAGES; i++) {
        const rows = await me.activity(PAGE, before);
        for (const e of rows) {
          if (e.ts < since) return { events: out, since, complete: true };
          out.push(e);
        }
        if (rows.length < PAGE) return { events: out, since, complete: true };
        before = rows[rows.length - 1].ts;
      }
      return { events: out, since: out.length ? out[out.length - 1].ts : since, complete: false };
    },
  });
}

const val = (e: ActivityEvent) => {
  const v = (e.detail as { value?: unknown } | null)?.value;
  return typeof v === "number" ? v : 0;
};

export interface CcSession {
  id: string;
  start: number;
  end: number;
  requests: number;
  usd: number;
  tokens: number;
  models: string[];
  commits: number;
  prs: number;
  added: number;
  removed: number;
  activeSeconds: number;
  toolAccepts: number;
  toolRejects: number;
  task: string | null;
  workflow: string | null;
  client: string | null;
}

/** Claude Code sessions from OTEL telemetry: cost, output (lines, commits, PRs) and tool decisions. */
export function ccSessions(events: ActivityEvent[]): CcSession[] {
  const by = new Map<string, CcSession & { _models: Set<string> }>();
  for (const e of events) {
    if (e.source !== "claude_code") continue;
    const id = e.session ?? "(no session)";
    let s = by.get(id);
    if (!s) {
      s = {
        id,
        start: e.ts,
        end: e.ts,
        requests: 0,
        usd: 0,
        tokens: 0,
        models: [],
        _models: new Set(),
        commits: 0,
        prs: 0,
        added: 0,
        removed: 0,
        activeSeconds: 0,
        toolAccepts: 0,
        toolRejects: 0,
        task: e.task,
        workflow: e.workflow,
        client: e.client,
      };
      by.set(id, s);
    }
    s.start = Math.min(s.start, e.ts);
    s.end = Math.max(s.end, e.ts);
    s.task ??= e.task;
    s.workflow ??= e.workflow;
    s.client ??= e.client;
    switch (e.kind) {
      case "cc.api_request":
        s.requests += 1;
        s.usd += e.usd ?? 0;
        s.tokens += e.tokens ?? 0;
        if (e.model) s._models.add(e.model);
        break;
      case "metric.commit":
        s.commits += val(e);
        break;
      case "metric.pull_request":
        s.prs += val(e);
        break;
      case "metric.lines_of_code":
        if (e.decision === "removed") s.removed += val(e);
        else s.added += val(e);
        break;
      case "metric.active_time":
        s.activeSeconds += val(e);
        break;
      case "cc.tool_decision":
        if (e.decision === "reject") s.toolRejects += 1;
        else s.toolAccepts += 1;
        break;
    }
  }
  return [...by.values()].map(({ _models, ...s }) => ({ ...s, models: [..._models] })).sort((a, b) => b.end - a.end);
}

/** Calls the policy stopped or changed (blocked, masked, warned). */
export const STOPPED = new Set(["block", "redact", "warn"]);
export const isStopped = (e: ActivityEvent) => e.kind.startsWith("check.") && !!e.decision && STOPPED.has(e.decision);
