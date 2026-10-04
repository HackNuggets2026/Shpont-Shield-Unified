import { useMemo, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { admin, type AdminAction } from "../../api";
import { ops, type OrgFields, type OrgIncident } from "../../opsApi";
import { countC } from "../../lib/compact";
import { ReasonDialog } from "../../components/Dialog";
import { IncidentStatusPill, LevelPill, PersonStatusPill, SeverityPill } from "../../components/pills";
import { RestrictActions } from "../../components/RestrictActions";
import { Button, Card, Empty, ErrorBox, Loading, PageHeader, Segmented, Stat, cx } from "../../components/ui";
import { IconArrowLeft, IconTerminal } from "../../components/icons";
import { EvidenceTimeline, buildTimeline, isEvidence } from "../../components/security/EvidenceTimeline";
import { RiskMeter } from "../../components/security/RiskMeter";
import { ViewContentButton } from "../../components/security/ViewContent";
import { ago, dateTime, pct } from "../../lib/format";
import { RULES, detectionPolicy, isAuto, responseLabel, ruleLabel, ruleWhat, severityFirst } from "../../lib/security";

type Next = "acknowledged" | "resolved" | "dismissed" | "open";

const NEXT_COPY: Record<Next, { label: string; body: string; tone: "primary" | "good" | "danger" }> = {
  acknowledged: { label: "Acknowledge", body: "Someone is looking at this. It keeps counting toward the person's risk score.", tone: "primary" },
  resolved: { label: "Resolve", body: "The issue was real and has been dealt with. Its weight stops counting toward the risk score.", tone: "good" },
  dismissed: { label: "Dismiss", body: "A false positive. Its weight stops counting toward the risk score.", tone: "primary" },
  open: { label: "Reopen", body: "Put the incident back in the open queue. Its weight counts again.", tone: "primary" },
};

function fmtGap(s: number): string {
  const a = Math.abs(Math.round(s));
  if (a < 60) return `${a}s`;
  if (a < 3600) return `${Math.round(a / 60)} min`;
  if (a < 86400) return `${(a / 3600).toFixed(1)} h`;
  return `${(a / 86400).toFixed(1)} days`;
}

const titleStatus = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function Step({ n, title, tone, children }: { n: number; title: string; tone: "bad" | "serious" | "neutral" | "good"; children: ReactNode }) {
  const dot = { bad: "bg-bad text-white", serious: "bg-serious text-white", neutral: "bg-ink/15 text-ink2", good: "bg-good text-white" }[tone];
  return (
    <div className="min-w-0 flex-1 p-4">
      <div className="mb-2 flex items-center gap-2">
        <span className={cx("grid h-5 w-5 shrink-0 place-items-center rounded-full text-[11px] font-semibold", dot)}>{n}</span>
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">{title}</span>
      </div>
      <div className="space-y-1.5 text-sm text-ink">{children}</div>
    </div>
  );
}

function Legend({ cls, children }: { cls: string; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={cx("h-2.5 w-[3px] rounded-full", cls)} />
      {children}
    </span>
  );
}

type PeersQ = UseQueryResult<{ rows: OrgIncident[]; total: number | null; hasMore: boolean }>;

/** Is this a one-off or a pattern? Others in the same team and department hit by the same rule in 30 days. */
function PeersCard({
  rule,
  self,
  incidentId,
  team,
  department,
  teamQ,
  deptQ,
  link,
}: {
  rule: string;
  self: string;
  incidentId: string;
  team: string;
  department: string;
  teamQ: PeersQ;
  deptQ: PeersQ;
  link: (p: Record<string, string>) => string;
}) {
  if (!team && !department) return null;
  const others = (q: PeersQ) => (q.data?.rows ?? []).filter((i) => i.principal !== self);
  const people = (q: PeersQ) => new Set(others(q).map((i) => i.principal)).size;
  const teamRows = others(teamQ)
    .filter((i) => i.id !== incidentId)
    .sort(severityFirst);
  // Matching incidents not about this person: the server's total minus this person's rows on the page.
  const count = (q: PeersQ) => (q.data ? (q.data.total ?? q.data.rows.length) - (q.data.rows.length - others(q).length) : null);
  const line = (label: string, q: PeersQ, n: number | null, to: string) => (
    <Link to={to} className="flex items-baseline justify-between gap-3 px-4 py-2 text-xs hover:bg-raised/60">
      <span className="min-w-0 truncate text-ink2">
        in <b className="text-ink">{label}</b>
      </span>
      <span className="tnum shrink-0 text-muted">
        {q.isPending ? "…" : q.isError ? "unavailable" : n ? (
          <>
            <b className={cx("text-ink", n > 0 && "text-serious")}>{countC(n)}</b> incident{n === 1 ? "" : "s"} · {countC(people(q))}
            {q.data?.hasMore ? "+" : ""} {people(q) === 1 && !q.data?.hasMore ? "person" : "people"}
          </>
        ) : (
          "nobody else"
        )}
      </span>
    </Link>
  );
  return (
    <Card title={`${ruleLabel(rule)} nearby`} subtitle="Others hit by the same rule in the last 30 days: a one-off, or a pattern?" flush>
      <div className="divide-y divide-line/60 border-b border-line">
        {team && line(`team ${team}`, teamQ, count(teamQ), link({ rule, team, ...(department ? { department } : {}) }))}
        {department && line(department, deptQ, count(deptQ), link({ rule, department }))}
      </div>
      {teamRows.length > 0 && (
        <ul className="divide-y divide-line/60">
          {teamRows.slice(0, 6).map((o) => (
            <li key={o.id}>
              <Link to={`/console/incidents/${encodeURIComponent(o.id)}`} className="flex items-center justify-between gap-2 px-4 py-2 hover:bg-raised/60">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-ink">{o.name || o.principal}</div>
                  <div className="text-[11px] text-muted" title={dateTime(o.ts)}>
                    {ago(o.ts)} · weight {Math.round(o.weight)}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <SeverityPill severity={o.severity} />
                  <IncidentStatusPill status={o.status} />
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function IncidentPage() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin", "incident", id], queryFn: () => admin.incident(id), refetchInterval: 5_000 });
  // Status changes are logged against the incident id, not the person, so the detail endpoint misses them.
  const own = useQuery({ queryKey: ["admin", "actions", id], queryFn: () => admin.actions(id), refetchInterval: 10_000 });
  const pol = useQuery({ queryKey: ["admin", "policy", "detections"], queryFn: detectionPolicy, staleTime: 60_000 });
  const pid = q.data?.principal.id;
  const person = useQuery({
    queryKey: ["admin", "person", pid, 30],
    queryFn: () => admin.person(pid!, 30),
    enabled: !!pid,
    refetchInterval: 15_000,
  });
  // Org context: the directory fields ride on the incident; the person's profile fills in for older servers.
  const inc0 = q.data?.incident as OrgIncident | undefined;
  const pd = person.data as (typeof person.data & OrgFields) | undefined;
  // The incident detail does not carry directory fields yet: look the person up in the directory.
  const dir = useQuery({
    queryKey: ["admin", "people", "lookup", pid],
    queryFn: async () => (await ops.people({ q: pid!, limit: 10 })).rows.find((r) => r.principal === pid) ?? null,
    enabled: !!pid && !inc0?.department,
    staleTime: 60_000,
  });
  const team = inc0?.team || dir.data?.team || pd?.team || "";
  const department = inc0?.department || dir.data?.department || pd?.department || "";
  const rule = inc0?.rule ?? "";
  const peersTeam = useQuery({
    queryKey: ["admin", "incidents", "peers", "team", rule, team],
    queryFn: () => ops.incidents({ rule, team, days: 30, limit: 12 }),
    enabled: !!rule && !!team,
    refetchInterval: 15_000,
  });
  const peersDept = useQuery({
    queryKey: ["admin", "incidents", "peers", "dept", rule, department],
    queryFn: () => ops.incidents({ rule, department, days: 30, limit: 100 }),
    enabled: !!rule && !!department,
    refetchInterval: 15_000,
  });
  const [next, setNext] = useState<Next | null>(null);
  const [only, setOnly] = useState<"all" | "key">("all");

  const actions = useMemo<AdminAction[]>(() => {
    const seen = new Set<string>();
    return [...(q.data?.actions ?? []), ...(own.data ?? [])].filter((a) => {
      const k = `${a.ts}|${a.actor}|${a.action}|${a.target}`;
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }, [q.data, own.data]);

  const sec = (p: Record<string, string>) => `/console/security?${new URLSearchParams({ status: "all", ...p })}`;
  const back = (
    <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-1">
      <Link to="/console/security" className="inline-flex items-center gap-1 hover:text-ink">
        <IconArrowLeft size={12} /> Security
      </Link>
      {department && (
        <>
          <span aria-hidden>›</span>
          <Link to={sec({ department })} className="hover:text-ink" title={`All incidents in ${department}`}>
            {department}
          </Link>
        </>
      )}
      {team && (
        <>
          <span aria-hidden>›</span>
          <Link to={sec(department ? { department, team } : { team })} className="hover:text-ink" title={`All incidents in team ${team}`}>
            {team}
          </Link>
        </>
      )}
    </nav>
  );
  if (q.isPending)
    return (
      <div>
        <PageHeader title="Incident" back={back} />
        <Loading rows={8} />
      </div>
    );
  if (q.isError)
    return (
      <div>
        <PageHeader title="Incident" back={back} subtitle={<span className="font-mono">{id}</span>} />
        <ErrorBox error={q.error} retry={() => q.refetch()} />
      </div>
    );

  const { incident: inc, timeline, principal } = q.data;
  const row = person.data;
  const who = inc0?.name || dir.data?.name || principal.id;
  const th = pol.data?.levels ?? null;
  const halfLife = pol.data ? pol.data.half_life_hours * 60 : null; // minutes
  const live = inc.status === "open" || inc.status === "acknowledged";
  const nowWeight = live && halfLife ? inc.weight * 0.5 ** ((Date.now() / 1000 - inc.ts) / (halfLife * 60)) : 0;

  const evidence = timeline.filter(isEvidence);
  const items = buildTimeline(timeline, actions);
  const keyItems = items.filter((it) => (it.t === "event" ? isEvidence(it.e) || it.e.source === "detections" : true));
  const shown = only === "key" ? keyItems : items;
  const ccCount = evidence.filter((e) => e.source === "claude_code").length;

  const autos = actions.filter(isAuto).sort((a, b) => a.ts - b.ts);
  const humans = actions.filter((a) => !isAuto(a));
  const others = (person.data?.incidents ?? []).filter((i) => i.id !== inc.id).sort(severityFirst);
  const scale = row?.budget_scale ?? 1;
  const restricted = principal.status !== "active" || scale < 1;

  const transitions: Next[] =
    inc.status === "open" ? ["acknowledged", "resolved", "dismissed"] : inc.status === "acknowledged" ? ["resolved", "dismissed", "open"] : ["open"];
  const window: [number, number] = [inc.ts - 3600, inc.ts + 900];

  return (
    <div className="space-y-4">
      <PageHeader
        back={back}
        title={
          <span className="flex flex-wrap items-center gap-2">
            {RULES[inc.rule]?.cc && <IconTerminal size={18} className="text-cc" />}
            <span className="truncate">{ruleLabel(inc.rule)}</span>
            <SeverityPill severity={inc.severity} />
            <IncidentStatusPill status={inc.status} />
          </span>
        }
        subtitle={
          <span className="flex flex-wrap items-center gap-x-1.5">
            <Link to={`/console/people/${encodeURIComponent(principal.id)}`} className="font-medium text-ink hover:text-accent">
              {who}
            </Link>
            {who !== principal.id && <span className="font-mono text-[11px]">({principal.id})</span>}
            {team && <span>· {team}</span>}
            {department && department !== team && <span>· {department}</span>}
            <span title={dateTime(inc.ts)}>
              · {dateTime(inc.ts)} ({ago(inc.ts)})
            </span>
            <span className="font-mono text-[11px]">· {inc.id}</span>
          </span>
        }
        actions={transitions.map((t) => (
          <Button key={t} variant={t === "resolved" ? "good" : t === "acknowledged" ? "primary" : "secondary"} onClick={() => setNext(t)}>
            {NEXT_COPY[t].label}
          </Button>
        ))}
      />

      {/* The story at a glance: detected, what the system did, where it stands now. */}
      <section className="overflow-hidden soft-card rounded-2xl">
        <div className={cx("border-b border-line px-4 py-3", inc.severity === "high" && live ? "bg-bad/[0.06]" : "bg-raised/40")}>
          <p className="break-words text-[15px] font-medium leading-snug text-ink">{inc.detail}</p>
          {ruleWhat(inc.rule) && <p className="mt-0.5 text-xs text-muted">{ruleLabel(inc.rule)}: {ruleWhat(inc.rule)}.</p>}
        </div>
        <div className="flex flex-col divide-y divide-line md:flex-row md:divide-x md:divide-y-0">
          <Step n={1} title="Detected" tone="bad">
            <div>
              <b>{evidence.length}</b> evidence event{evidence.length === 1 ? "" : "s"}
              {inc.evidence.length !== evidence.length && <span className="text-muted"> ({inc.evidence.length} named)</span>}
              {ccCount > 0 && <span className="text-cc"> · {ccCount} from Claude Code</span>}
            </div>
            <div>
              Weight <b className="tnum">+{Math.round(inc.weight)}</b> on {principal.id}'s risk
              {live && halfLife ? <span className="text-muted"> (worth {Math.round(nowWeight)} now; halves every {fmtGap(halfLife * 60)})</span> : null}
            </div>
          </Step>
          <Step n={2} title="System responded" tone={autos.length ? "serious" : "neutral"}>
            {autos.length === 0 ? (
              <div className="text-ink2">No automatic action{th ? `: the score moves the person to Watch at ${th.watch} and Restricted at ${th.restricted} on its own` : ""}.</div>
            ) : (
              <ul className="space-y-1">
                {autos.map((a, i) => (
                  <li key={i} className="flex flex-wrap items-baseline gap-x-1.5">
                    <b>{responseLabel(a)}</b>
                    <span className="text-xs text-muted" title={dateTime(a.ts)}>
                      {a.ts >= inc.ts ? `${fmtGap(a.ts - inc.ts)} after detection` : `${fmtGap(inc.ts - a.ts)} before`}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {autos.length > 0 && <div className="text-xs text-muted">by {[...new Set(autos.map((a) => a.actor))].join(", ")}, no human in the loop</div>}
          </Step>
          <Step n={3} title="Now" tone={live ? (principal.status === "active" && scale >= 1 ? "bad" : "serious") : "good"}>
            <div className="flex flex-wrap items-center gap-1.5">
              <PersonStatusPill status={principal.status} scale={row?.budget_scale} />
              <LevelPill level={principal.level} />
              <span className="text-xs text-muted">risk {Math.round(principal.risk)}</span>
            </div>
            <div className="text-ink2">
              Incident <b>{inc.status}</b>
              {live
                ? humans.length
                  ? `; ${humans.length} admin action${humans.length === 1 ? "" : "s"} so far.`
                  : "; waiting for a human."
                : inc.note
                  ? `: “${inc.note}”`
                  : "."}
            </div>
          </Step>
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="min-w-0 xl:col-span-2">
          <Card
            flush
            title="Evidence timeline"
            subtitle="An hour before to 15 minutes after, with every response. T is the moment of detection."
            actions={
              <Segmented
                value={only}
                onChange={setOnly}
                options={[
                  { value: "all", label: `All (${items.length})` },
                  { value: "key", label: `Key moments (${keyItems.length})` },
                ]}
              />
            }
          >
            <div className="flex flex-wrap gap-x-4 gap-y-1 border-b border-line px-4 py-2 text-[11px] text-muted">
              <Legend cls="bg-bad">evidence</Legend>
              <Legend cls="bg-accent">this detection</Legend>
              <Legend cls="bg-serious">auto response</Legend>
              <Legend cls="bg-info">admin</Legend>
              <Legend cls="bg-cc">Claude Code</Legend>
            </div>
            {shown.length === 0 ? (
              <Empty title="No events in the window" hint="The evidence may have aged out of the activity store." />
            ) : (
              <div className="xl:max-h-[680px] xl:overflow-y-auto">
                <EvidenceTimeline items={shown} incidentId={inc.id} origin={inc.ts} />
              </div>
            )}
          </Card>
        </div>

        <div className="min-w-0 space-y-4">
          <Card
            title="Person"
            actions={
              <Link to={`/console/people/${encodeURIComponent(principal.id)}`} className="text-xs font-medium text-accent hover:underline">
                Open profile →
              </Link>
            }
          >
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <span className="text-base font-semibold text-ink">{who}</span>
              <PersonStatusPill status={principal.status} scale={row?.budget_scale} />
              <LevelPill level={principal.level} />
            </div>
            {row && (
              <div className="mb-3 text-xs text-muted">
                {who !== principal.id && <span className="font-mono">{principal.id} · </span>}
                {[team, department !== team ? department : "", row.role !== "?" ? row.role : ""].filter(Boolean).join(" · ")} · {row.open_incidents} open incident{row.open_incidents === 1 ? "" : "s"}
              </div>
            )}
            <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">Risk score</div>
            <RiskMeter score={principal.risk} thresholds={th} />
            {restricted && row?.reason && (
              <div className="mt-3 rounded-lg bg-bad/[0.07] px-3 py-2 text-xs text-ink2 ring-1 ring-inset ring-bad/20">
                <span className="font-medium text-ink">{principal.status === "active" ? `Budget at ${pct(scale, 0)}` : titleStatus(principal.status)}</span> by{" "}
                {row.by || "?"}
                {row.since ? ` ${ago(row.since)}` : ""}: “{row.reason}”
              </div>
            )}
            <div className="mt-4 space-y-2">
              <RestrictActions pid={principal.id} status={principal.status} scale={scale} level={principal.level} compact />
              <ViewContentButton
                pid={principal.id}
                incidentId={inc.id}
                evidence={inc.evidence}
                window={window}
                suggestion={`Investigating incident ${inc.id} (${ruleLabel(inc.rule).toLowerCase()})`}
              />
            </div>
            {restricted && (
              <p className="mt-3 text-[11px] text-muted">Closing incidents lowers the score but never lifts a restriction. Restore lifts it and resolves the open incidents.</p>
            )}
          </Card>

          <Card title="Detection">
            <div className="grid grid-cols-2 gap-3">
              <Stat label="Rule" value={<span className="font-mono text-xs">{inc.rule}</span>} />
              <Stat label="Severity" value={inc.severity} />
              <Stat label="Weight" value={Math.round(inc.weight)} />
              <Stat label="Counts now" value={live ? (halfLife ? Math.round(nowWeight) : "yes") : "no, closed"} />
            </div>
            {inc.note && (
              <div className="mt-3 rounded-lg bg-raised px-3 py-2 text-xs text-ink2">
                <span className="font-medium text-ink">Note:</span> {inc.note}
              </div>
            )}
          </Card>

          <Card title={`Other incidents for ${who}`} flush>
            {person.isPending ? (
              <div className="p-4">
                <Loading rows={3} />
              </div>
            ) : person.isError ? (
              <div className="p-4">
                <ErrorBox error={person.error} compact retry={() => person.refetch()} />
              </div>
            ) : others.length === 0 ? (
              <Empty title="None in the last 7 days" />
            ) : (
              <ul className="divide-y divide-line/60">
                {others.slice(0, 8).map((o) => (
                  <li key={o.id}>
                    <Link to={`/console/incidents/${encodeURIComponent(o.id)}`} className="flex items-center justify-between gap-2 px-4 py-2 hover:bg-raised/60">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-ink">{ruleLabel(o.rule)}</div>
                        <div className="text-[11px] text-muted" title={dateTime(o.ts)}>
                          {ago(o.ts)} · weight {Math.round(o.weight)}
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-1">
                        <SeverityPill severity={o.severity} />
                        <IncidentStatusPill status={o.status} />
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <PeersCard
            rule={inc.rule}
            self={principal.id}
            incidentId={inc.id}
            team={team}
            department={department}
            teamQ={peersTeam}
            deptQ={peersDept}
            link={sec}
          />
        </div>
      </div>

      <ReasonDialog
        open={!!next}
        onClose={() => setNext(null)}
        title={next ? `${NEXT_COPY[next].label} · ${ruleLabel(inc.rule)} (${principal.id})` : ""}
        description={next ? NEXT_COPY[next].body : ""}
        reasonLabel="Note"
        reasonHint="Kept on the incident and in the admin log."
        reasonRequired={next === "resolved" || next === "dismissed"}
        placeholder={next === "dismissed" ? "e.g. Confirmed with the manager: approved data migration." : `e.g. Spoke with ${principal.id}; export deleted.`}
        confirmLabel={next ? NEXT_COPY[next].label : ""}
        tone={next ? NEXT_COPY[next].tone : "primary"}
        onConfirm={async (note) => {
          await admin.setIncident(inc.id, next!, note);
          await qc.invalidateQueries({ queryKey: ["admin"] });
        }}
      >
        {(next === "resolved" || next === "dismissed") && restricted && (
          <div className="rounded-lg bg-warn/10 px-3 py-2 text-xs text-ink2 ring-1 ring-inset ring-warn/30">
            {principal.id} stays <b>{principal.status === "active" ? "limited" : principal.status}</b> after this. Use Restore on the person to lift it.
          </div>
        )}
      </ReasonDialog>
    </div>
  );
}
