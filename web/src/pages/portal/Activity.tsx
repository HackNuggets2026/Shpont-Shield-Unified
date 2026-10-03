import { useMemo, useState } from "react";
import { me, type ActivityEvent, type Breakdown } from "../../api";
import { ActivityFeed } from "../../components/ActivityFeed";
import { seriesLabel } from "../../components/charts";
import { DecisionPill } from "../../components/pills";
import { Button, Card, Empty, ErrorBox, Kpi, Loading, PageHeader, Pill, Q } from "../../components/ui";
import { IconTerminal } from "../../components/icons";
import { explainReason } from "../../components/portal/explain";
import { ccSessions, isStopped, useMyEvents, type CcSession } from "../../components/portal/useMyEvents";
import { ago, dateTime, minutes, num, tokens, usd } from "../../lib/format";
import { kindLabel } from "../../lib/events";
import { useMe } from "./Home";

// ---- runs grouped by task -----------------------------------------------------------------------

function RunsByTask({ runs }: { runs: Breakdown[] }) {
  const groups = useMemo(() => {
    const by = new Map<string, { workflow: string; usd: number; requests: number; tasks: Breakdown[] }>();
    for (const r of runs) {
      if (!r.usd && !r.tokens && !r.requests && !r.minutes) continue;
      const wf = String(r.workflow ?? "(none)");
      const g = by.get(wf) ?? { workflow: wf, usd: 0, requests: 0, tasks: [] };
      g.usd += r.usd || 0;
      g.requests += r.requests || 0;
      g.tasks.push(r);
      by.set(wf, g);
    }
    return [...by.values()].sort((a, b) => b.usd - a.usd);
  }, [runs]);
  if (!groups.length) return <Empty title="No runs this week" hint="Calls you make through the gateway, MCP tools or Claude Code show up here." />;
  return (
    <div className="max-h-[440px] divide-y divide-line overflow-y-auto">
      {groups.map((g) => (
        <section key={g.workflow}>
          <header className="sticky top-0 z-[1] flex items-baseline justify-between gap-2 bg-raised/90 px-4 py-1.5 backdrop-blur">
            <span className="text-xs font-semibold text-ink">{seriesLabel(g.workflow)}</span>
            <span className="tnum text-xs text-ink2">
              {usd(g.usd)} · {g.tasks.length} task{g.tasks.length === 1 ? "" : "s"}
            </span>
          </header>
          <ul className="divide-y divide-line/50">
            {[...g.tasks]
              .sort((a, b) => b.usd - a.usd)
              .map((r, i) => (
                <li key={`${r.task}-${i}`} className="flex items-center justify-between gap-3 px-4 py-2 text-sm">
                  <div className="min-w-0">
                    <div className="truncate font-mono text-xs text-ink">{r.task === "(none)" ? <span className="font-sans text-muted">no task label</span> : r.task}</div>
                    <div className="text-[11px] text-muted">
                      {num(r.requests)} calls · {tokens(r.tokens)} tokens{r.minutes ? ` · ${minutes(r.minutes)} of resources` : ""}
                    </div>
                  </div>
                  <span className="tnum shrink-0 font-medium text-ink">{usd(r.usd)}</span>
                </li>
              ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

// ---- blocked and masked -------------------------------------------------------------------------

function StoppedRow({ e }: { e: ActivityEvent }) {
  const reason = (e.detail as { reason?: string } | null)?.reason;
  const x = explainReason(reason, e.decision);
  const where = e.tool ? `tool ${e.tool}` : e.model ?? e.resource ?? "";
  return (
    <li className="px-4 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <DecisionPill decision={e.decision === "redact" ? "redact" : e.decision} />
        <span className="font-medium text-ink">{x.title}</span>
      </div>
      {x.hint && <div className="mt-1 text-xs text-ink2">{x.hint}</div>}
      <div className="mt-1 flex flex-wrap gap-x-2 text-[11px] text-muted">
        <span title={dateTime(e.ts)}>{ago(e.ts)}</span>
        <span>{kindLabel(e.kind)}</span>
        {where && <span>{where}</span>}
        {e.workflow && <span>{seriesLabel(e.workflow)}</span>}
        {e.task && <span className="font-mono">{e.task}</span>}
        {x.code && <span className="font-mono">{x.code}</span>}
      </div>
      {x.extra && <div className="mt-1 break-words text-[11px] text-muted">Policy note: {x.extra}</div>}
    </li>
  );
}

function StoppedCard({ events, loading, error, retry }: { events: ActivityEvent[]; loading: boolean; error: unknown; retry: () => void }) {
  const rows = events.filter(isStopped);
  const blocked = rows.filter((e) => e.decision === "block").length;
  const masked = rows.filter((e) => e.decision === "redact").length;
  return (
    <Card
      title="Blocked or changed, and why"
      subtitle="Last 30 days. The text of a blocked prompt is never stored, only the reason."
      flush
      actions={
        rows.length ? (
          <span className="flex gap-1.5">
            {blocked > 0 && <Pill tone="bad">{blocked} blocked</Pill>}
            {masked > 0 && <Pill tone="serious">{masked} masked</Pill>}
          </span>
        ) : undefined
      }
    >
      {loading ? (
        <div className="p-4">
          <Loading rows={4} />
        </div>
      ) : error ? (
        <div className="p-4">
          <ErrorBox error={error} retry={retry} />
        </div>
      ) : rows.length === 0 ? (
        <Empty title="Nothing was blocked or changed" hint="Every call in the last 30 days went through as sent." />
      ) : (
        <ul className="max-h-[440px] divide-y divide-line/60 overflow-y-auto">
          {rows.map((e) => (
            <StoppedRow key={e.id} e={e} />
          ))}
        </ul>
      )}
    </Card>
  );
}

// ---- Claude Code ------------------------------------------------------------------------------

function SessionRow({ s }: { s: CcSession }) {
  return (
    <li className="px-4 py-3 text-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2">
          <span className="font-medium text-ink">{s.workflow ? seriesLabel(s.workflow) : "Unlabeled session"}</span>
          {s.task && <span className="font-mono text-xs text-ink2">{s.task}</span>}
        </div>
        <span className="tnum shrink-0 font-medium text-ink">{usd(s.usd)}</span>
      </div>
      <div className="mt-0.5 text-[11px] text-muted" title={`${dateTime(s.start)} → ${dateTime(s.end)}`}>
        {ago(s.end)} · {s.models.join(", ") || "no model calls"} · {num(s.requests)} requests
        {s.activeSeconds ? ` · ${minutes(s.activeSeconds / 60)} active` : ""}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-ink2">
        <span className="tnum">
          <span className="text-good">+{num(s.added)}</span> <span className="text-bad">−{num(s.removed)}</span> lines
        </span>
        <span className="tnum">
          {num(s.commits)} commit{s.commits === 1 ? "" : "s"}
        </span>
        {s.prs > 0 && <span className="tnum">{num(s.prs)} PR{s.prs === 1 ? "" : "s"}</span>}
        {s.toolRejects > 0 && (
          <span className="text-muted">
            {s.toolRejects} tool call{s.toolRejects === 1 ? "" : "s"} you declined
          </span>
        )}
      </div>
    </li>
  );
}

function ClaudeCodeCard({ events, loading, error, retry, partial }: { events: ActivityEvent[]; loading: boolean; error: unknown; retry: () => void; partial: boolean }) {
  const sessions = useMemo(() => ccSessions(events), [events]);
  const [all, setAll] = useState(false);
  const t = sessions.reduce(
    (a, s) => ({ usd: a.usd + s.usd, added: a.added + s.added, removed: a.removed + s.removed, commits: a.commits + s.commits, prs: a.prs + s.prs }),
    { usd: 0, added: 0, removed: 0, commits: 0, prs: 0 },
  );
  const shown = all ? sessions : sessions.slice(0, 8);
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
      subtitle={`Last 30 days${partial ? " (most recent part)" : ""}, from Claude Code telemetry. Your prompts are never stored.`}
    >
      {loading ? (
        <div className="p-4">
          <Loading rows={4} />
        </div>
      ) : error ? (
        <div className="p-4">
          <ErrorBox error={error} retry={retry} />
        </div>
      ) : sessions.length === 0 ? (
        <Empty title="No Claude Code sessions" hint="Sessions appear once Claude Code sends its telemetry to the gateway." />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 border-b border-line px-4 py-3 text-xs sm:grid-cols-5">
            <div>
              <div className="text-muted">Sessions</div>
              <div className="tnum mt-0.5 text-base font-semibold text-ink">{sessions.length}</div>
            </div>
            <div>
              <div className="text-muted">Cost</div>
              <div className="tnum mt-0.5 text-base font-semibold text-ink">{usd(t.usd)}</div>
            </div>
            <div>
              <div className="text-muted">Lines</div>
              <div className="tnum mt-0.5 text-base font-semibold">
                <span className="text-good">+{num(t.added)}</span> <span className="text-bad">−{num(t.removed)}</span>
              </div>
            </div>
            <div>
              <div className="text-muted">Commits</div>
              <div className="tnum mt-0.5 text-base font-semibold text-ink">{num(t.commits)}</div>
            </div>
            <div>
              <div className="text-muted">Pull requests</div>
              <div className="tnum mt-0.5 text-base font-semibold text-ink">{num(t.prs)}</div>
            </div>
          </div>
          <ul className="divide-y divide-line/60">
            {shown.map((s) => (
              <SessionRow key={s.id} s={s} />
            ))}
          </ul>
          {sessions.length > 8 && (
            <div className="flex justify-center border-t border-line p-2">
              <Button size="sm" variant="ghost" onClick={() => setAll((x) => !x)}>
                {all ? "Show fewer" : `Show all ${sessions.length} sessions`}
              </Button>
            </div>
          )}
        </>
      )}
    </Card>
  );
}

export function PortalActivity() {
  const q = useMe();
  const ev = useMyEvents(30);
  const events = ev.data?.events ?? [];
  const stopped = events.filter(isStopped).length;
  const cc = events.filter((e) => e.source === "claude_code" && e.kind === "cc.api_request").reduce((a, e) => a + (e.usd ?? 0), 0);
  const tasks = (q.data?.runs ?? []).filter((r) => r.task !== "(none)" && (r.usd || r.requests)).length;
  return (
    <div className="space-y-4">
      <PageHeader title="My activity" subtitle="What you ran, what it cost, and anything that was blocked or changed, with the reason." />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="Spend this week" value={q.data ? usd(q.data.by_workflow.reduce((a, r) => a + (r.usd || 0), 0)) : "…"} sub="all sources" />
        <Kpi label="Tasks this week" value={q.data ? (tasks >= 30 ? "30+" : tasks) : "…"} sub="with a task label" />
        <Kpi label="Claude Code, 30 days" value={ev.data ? usd(cc) : "…"} sub="model requests" />
        <Kpi label="Blocked or changed" value={ev.data ? stopped : "…"} tone={stopped ? "warn" : undefined} sub="last 30 days" />
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Runs by task" subtitle="Last 7 days, grouped by workflow (top 30 tasks)" flush>
          <Q q={q} rows={5}>
            {(s) => <RunsByTask runs={s.runs} />}
          </Q>
        </Card>
        <StoppedCard events={events} loading={ev.isPending} error={ev.error} retry={() => ev.refetch()} />
      </div>
      <ClaudeCodeCard events={events} loading={ev.isPending} error={ev.error} retry={() => ev.refetch()} partial={ev.data ? !ev.data.complete : false} />
      <Card title="Everything recorded under your key" subtitle="Newest first. Metadata only: who, what, cost and decision." flush>
        <ActivityFeed load={(p) => me.activity(p.limit, p.before)} queryKey={["me", "activity"]} serverFilters={false} interval={5000} />
      </Card>
    </div>
  );
}
