import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { me, type ActivityEvent } from "../../api";
import { ActivityFeed } from "../../components/ActivityFeed";
import { seriesLabel } from "../../components/charts";
import { DecisionPill } from "../../components/pills";
import { Card, Empty, ErrorBox, Loading, PageHeader, Q, TableWrap } from "../../components/ui";
import { IconTerminal } from "../../components/icons";
import { ago, dateTime, num, tokens, usd } from "../../lib/format";
import { kindLabel } from "../../lib/events";
import { useMe } from "./Home";

interface CcSession {
  id: string;
  start: number;
  end: number;
  requests: number;
  usd: number;
  tokens: number;
  models: Set<string>;
  commits: number;
  lines: number;
  task: string | null;
  workflow: string | null;
  client: string | null;
}

function ccSessions(events: ActivityEvent[]): CcSession[] {
  const by = new Map<string, CcSession>();
  for (const e of events) {
    if (e.source !== "claude_code") continue;
    const id = e.session ?? "(no session)";
    let s = by.get(id);
    if (!s) {
      s = { id, start: e.ts, end: e.ts, requests: 0, usd: 0, tokens: 0, models: new Set(), commits: 0, lines: 0, task: e.task, workflow: e.workflow, client: e.client };
      by.set(id, s);
    }
    s.start = Math.min(s.start, e.ts);
    s.end = Math.max(s.end, e.ts);
    s.task ??= e.task;
    s.workflow ??= e.workflow;
    const v = (e.detail as { value?: number } | null)?.value ?? 0;
    if (e.kind === "cc.api_request") {
      s.requests += 1;
      s.usd += e.usd ?? 0;
      s.tokens += e.tokens ?? 0;
      if (e.model) s.models.add(e.model);
    } else if (e.kind === "metric.commit") s.commits += v;
    else if (e.kind === "metric.lines_of_code" && e.decision === "added") s.lines += v;
  }
  return [...by.values()].sort((a, b) => b.end - a.end);
}

const STOPPED = new Set(["block", "redact", "warn"]);

/** Calls the policy stopped or changed. Built from the activity stream, which survives restarts. */
function BlockedCard() {
  const q = useQuery({ queryKey: ["me", "activity", 500], queryFn: () => me.activity(500), refetchInterval: 30_000 });
  const rows = (q.data ?? []).filter((e) => e.kind.startsWith("check.") && e.decision && STOPPED.has(e.decision));
  return (
    <Card title="Blocked and flagged" subtitle="Calls the policy stopped or changed, and why" flush>
      {q.isPending ? (
        <div className="p-4">
          <Loading rows={4} />
        </div>
      ) : q.isError ? (
        <div className="p-4">
          <ErrorBox error={q.error} retry={() => q.refetch()} />
        </div>
      ) : rows.length === 0 ? (
        <Empty title="Nothing blocked" hint="Every recent call went through untouched." />
      ) : (
        <ul className="max-h-[380px] divide-y divide-line/60 overflow-y-auto">
          {rows.map((e) => (
            <li key={e.id} className="px-4 py-2.5 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <DecisionPill decision={e.decision} />
                <span className="text-xs text-ink2">
                  {kindLabel(e.kind)}
                  {e.tool ? ` · ${e.tool}` : e.model ? ` · ${e.model}` : ""}
                  {e.workflow ? ` · ${seriesLabel(e.workflow)}` : ""}
                </span>
                <span className="ml-auto text-[11px] text-muted" title={dateTime(e.ts)}>
                  {ago(e.ts)}
                </span>
              </div>
              <div className="mt-1 break-words text-xs text-ink2">{String((e.detail as { reason?: string } | null)?.reason || "—")}</div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function ClaudeCodeCard() {
  const q = useQuery({ queryKey: ["me", "activity", 500], queryFn: () => me.activity(500), refetchInterval: 30_000 });
  const sessions = useMemo(() => ccSessions(q.data ?? []), [q.data]);
  const total = sessions.reduce((a, s) => a + s.usd, 0);
  return (
    <Card
      flush
      title={
        <span className="flex items-center gap-1.5">
          <span className="text-cc">
            <IconTerminal size={15} />
          </span>
          Claude Code sessions
        </span>
      }
      subtitle={q.data ? `${sessions.length} sessions · ${usd(total)} in your last ${q.data.length} events` : "From Claude Code telemetry"}
    >
      {q.isPending ? (
        <div className="p-4">
          <Loading rows={4} />
        </div>
      ) : q.isError ? (
        <div className="p-4">
          <ErrorBox error={q.error} retry={() => q.refetch()} />
        </div>
      ) : sessions.length === 0 ? (
        <Empty title="No Claude Code sessions" hint="Sessions appear here once Claude Code telemetry points at the gateway." />
      ) : (
        <TableWrap maxH="380px">
          <table className="tbl min-w-[760px]">
            <thead>
              <tr>
                <th>Session</th>
                <th>Task</th>
                <th>Models</th>
                <th className="text-right">Requests</th>
                <th className="text-right">Tokens</th>
                <th className="text-right">Commits</th>
                <th className="text-right">Lines +</th>
                <th className="text-right">Cost</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.id}>
                  <td>
                    <div className="font-mono text-[11px] text-ink2">{s.id.slice(0, 8)}</div>
                    <div className="text-[11px] text-muted" title={`${dateTime(s.start)} → ${dateTime(s.end)}`}>
                      {ago(s.end)}
                    </div>
                  </td>
                  <td className="text-xs">
                    <div>{s.workflow ? seriesLabel(s.workflow) : "—"}</div>
                    {s.task && <div className="font-mono text-[11px] text-muted">{s.task}</div>}
                  </td>
                  <td className="font-mono text-[11px] text-ink2">{[...s.models].join(", ") || "—"}</td>
                  <td className="tnum text-right">{num(s.requests)}</td>
                  <td className="tnum text-right">{tokens(s.tokens)}</td>
                  <td className="tnum text-right">{num(s.commits)}</td>
                  <td className="tnum text-right">{num(s.lines)}</td>
                  <td className="tnum text-right font-medium">{usd(s.usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </Card>
  );
}

export function PortalActivity() {
  const q = useMe();
  return (
    <div className="space-y-4">
      <PageHeader title="My activity" subtitle="What you ran, what it cost, and anything that was blocked and why." />
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Runs by task" subtitle="Last 7 days" flush>
          <Q q={q} rows={5}>
            {(s) =>
              s.runs.filter((r) => r.usd || r.tokens || r.requests).length === 0 ? (
                <Empty title="No runs this week" />
              ) : (
                <TableWrap maxH="380px">
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Workflow</th>
                        <th>Task</th>
                        <th className="text-right">Calls</th>
                        <th className="text-right">Tokens</th>
                        <th className="text-right">Cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {s.runs
                        .filter((r) => r.usd || r.tokens || r.requests)
                        .map((r, i) => (
                          <tr key={i}>
                            <td className="font-medium">{seriesLabel(String(r.workflow))}</td>
                            <td className="font-mono text-xs">{r.task === "(none)" ? <span className="text-muted">no task label</span> : r.task}</td>
                            <td className="tnum text-right">{num(r.requests)}</td>
                            <td className="tnum text-right">{tokens(r.tokens)}</td>
                            <td className="tnum text-right font-medium">{usd(r.usd)}</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </TableWrap>
              )
            }
          </Q>
        </Card>
        <BlockedCard />
      </div>
      <ClaudeCodeCard />
      <Card title="Activity feed" subtitle="Everything recorded under your key, newest first" flush>
        <ActivityFeed load={(p) => me.activity(p.limit, p.before)} queryKey={["me", "activity"]} serverFilters={false} interval={5000} />
      </Card>
    </div>
  );
}
