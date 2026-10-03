import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { admin, type Breakdown } from "../../api";
import { ActivityFeed } from "../../components/ActivityFeed";
import { AdminLog } from "../../components/AdminLog";
import { BarList, StackedChart, seriesLabel } from "../../components/charts";
import { GrantDialog, GrantsTable } from "../../components/Grants";
import { IncidentTable } from "../../components/IncidentList";
import { LeasesTable } from "../../components/Leases";
import { LevelPill, PersonStatusPill, RequestStatusPill, sourceLabel } from "../../components/pills";
import { RestrictActions, ViewEventsButton } from "../../components/RestrictActions";
import { Button, Card, Empty, ErrorBox, Kpi, Loading, PageHeader, TableWrap } from "../../components/ui";
import { IconArrowLeft, IconKey } from "../../components/icons";
import { ago, dateTime, num, pct, tokens, usd } from "../../lib/format";
import { RiskBar } from "./People";

export function BreakdownTable({ rows, dim, empty = "No usage" }: { rows: Breakdown[]; dim: string; empty?: string }) {
  const shown = rows.filter((r) => r.usd || r.tokens || r.requests || r.minutes);
  if (!shown.length) return <Empty title={empty} />;
  return (
    <TableWrap>
      <table className="tbl">
        <thead>
          <tr>
            <th>{dim}</th>
            <th className="text-right">Calls</th>
            <th className="text-right">Tokens</th>
            <th className="text-right">Minutes</th>
            <th className="text-right">Cost</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((r, i) => (
            <tr key={`${r[dim.toLowerCase()]}-${i}`}>
              <td className="font-medium text-ink">{seriesLabel(String(r[dim.toLowerCase()] ?? "(none)"))}</td>
              <td className="tnum text-right">{num(r.requests)}</td>
              <td className="tnum text-right">{tokens(r.tokens)}</td>
              <td className="tnum text-right">{r.minutes ? num(r.minutes, 1) : "—"}</td>
              <td className="tnum text-right font-medium">{usd(r.usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

export function PersonPage() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin", "person", id], queryFn: () => admin.person(id), refetchInterval: 10_000 });
  const [granting, setGranting] = useState(false);

  const back = (
    <Link to="/console/people" className="inline-flex items-center gap-1 hover:text-ink">
      <IconArrowLeft size={12} /> People
    </Link>
  );
  if (q.isPending)
    return (
      <div>
        <PageHeader title={id} back={back} />
        <Loading rows={8} />
      </div>
    );
  if (q.isError)
    return (
      <div>
        <PageHeader title={id} back={back} />
        <ErrorBox error={q.error} retry={() => q.refetch()} />
      </div>
    );
  const p = q.data;
  const total30 = p.spend.totals.reduce((a, b) => a + b, 0);
  const restricted = p.status !== "active" || p.budget_scale < 1;

  return (
    <div className="space-y-4">
      <PageHeader
        back={back}
        title={
          <span className="flex flex-wrap items-center gap-2">
            {p.principal}
            <PersonStatusPill status={p.status} scale={p.budget_scale} />
            <LevelPill level={p.level} />
          </span>
        }
        subtitle={[p.team, p.role, p.email].filter(Boolean).join(" · ")}
        actions={
          <>
            <ViewEventsButton pid={p.principal} />
            <Button onClick={() => setGranting(true)}>
              <IconKey size={14} /> Grant access
            </Button>
          </>
        }
      />

      {restricted && (
        <div className={`rounded-xl px-4 py-3 text-sm ring-1 ring-inset ${p.status === "active" ? "bg-warn/10 ring-warn/30" : "bg-bad/10 ring-bad/30"}`}>
          <div className="font-medium text-ink">
            {p.status === "active" ? `Budget limited to ${pct(p.budget_scale, 0)} of normal` : `Access ${p.status}`}
          </div>
          <div className="mt-0.5 text-xs text-ink2">
            {p.reason || "no reason recorded"} · by {p.by || "unknown"} {p.since ? `· ${ago(p.since)} (${dateTime(p.since)})` : ""}
          </div>
        </div>
      )}

      <Card title="Restrict or restore" subtitle="Every action needs a reason; the person sees it on their privacy page.">
        <RestrictActions pid={p.principal} status={p.status} scale={p.budget_scale} />
      </Card>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Kpi label="Risk score" value={<RiskBar score={p.risk} />} sub={`level ${p.level === "none" ? "normal" : p.level}`} />
        <Kpi label="Spend, 30 days" value={usd(total30)} sub={`today ${usd(p.today.usd)}`} />
        <Kpi label="Calls today" value={num(p.today.requests)} sub={`${tokens(p.today.tokens)} tokens`} />
        <Kpi
          label="Policy adherence"
          value={p.adherence ? pct(p.adherence.adherence) : "—"}
          sub={p.adherence ? `${num(p.adherence.total)} checks · ${num(p.adherence.block)} blocked` : "no checks"}
          tone={p.adherence && (p.adherence.adherence ?? 1) < 0.9 ? "bad" : undefined}
        />
        <Kpi label="Open incidents" value={p.open_incidents} tone={p.open_incidents ? "bad" : undefined} sub={`${p.incidents.length} in total`} />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Spend by workflow, 30 days" className="xl:col-span-2">
          <StackedChart ts={p.spend} height={220} />
        </Card>
        <Card title="By source and resource" subtitle="30 days">
          <div className="space-y-5">
            <BarList rows={p.by_source.filter((r) => r.usd > 0).map((r) => ({ key: String(r.source), label: sourceLabel(String(r.source)), value: r.usd }))} fmt={(v) => usd(v)} empty="No metered spend" />
            <div className="border-t border-line pt-4">
              <BarList
                tone="var(--s3)"
                rows={p.by_resource.filter((r) => r.usd > 0).map((r) => ({ key: String(r.resource), value: r.usd }))}
                fmt={(v) => usd(v)}
                empty="No resource spend"
              />
            </div>
          </div>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Workflows" subtitle="30 days" flush>
          <BreakdownTable rows={p.by_workflow} dim="Workflow" empty="No workflow usage" />
        </Card>
        <Card title="Incidents" flush>
          <IncidentTable incidents={p.incidents} showPerson={false} empty="No incidents for this person" />
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Running resources" flush>
          <LeasesTable
            leases={p.leases}
            showPerson={false}
            onStop={async (lid) => {
              const r = await admin.releaseLease(lid);
              await qc.invalidateQueries({ queryKey: ["admin"] });
              return r;
            }}
          />
        </Card>
        <Card title="Access grants" flush>
          <GrantsTable
            grants={p.grants.map((g) => ({ ...g, principal: p.principal }))}
            showPerson={false}
            empty="No grants"
            onRevoke={async (g, reason) => {
              await admin.revokeGrant(p.principal, g.id, reason);
              await qc.invalidateQueries({ queryKey: ["admin"] });
            }}
          />
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Requests" flush>
          {p.requests.length === 0 ? (
            <Empty title="No requests" />
          ) : (
            <TableWrap>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Kind</th>
                    <th>For</th>
                    <th>Reason</th>
                    <th>Status</th>
                    <th>When</th>
                  </tr>
                </thead>
                <tbody>
                  {p.requests.map((r) => (
                    <tr key={r.id}>
                      <td className="capitalize">{r.kind}</td>
                      <td className="font-mono text-xs">{r.workflow ?? r.detail?.resource ?? (r.scale ? `${r.scale}× budget` : "—")}</td>
                      <td className="max-w-[220px] truncate text-xs" title={r.reason}>
                        {r.reason}
                      </td>
                      <td>
                        <RequestStatusPill status={r.status} />
                      </td>
                      <td className="text-xs text-muted">{ago(r.ts)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
        </Card>
        <Card title="Admin actions about this person" flush>
          <AdminLog actions={p.admin_actions} showTarget={false} />
        </Card>
      </div>

      <Card title="Activity" subtitle="Metadata only. Content needs “View events” with a reason." flush>
        <ActivityFeed load={admin.activity} queryKey={["admin", "activity", "person", id]} fixed={{ principal: id }} maxH="420px" />
      </Card>

      <GrantDialog open={granting} onClose={() => setGranting(false)} principal={p.principal} />
    </div>
  );
}
