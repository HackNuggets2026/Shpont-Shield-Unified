import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { me, type BudgetScope, type MeSummary } from "../../api";
import { AdminLog } from "../../components/AdminLog";
import { StackedChart } from "../../components/charts";
import { LeasesTable } from "../../components/Leases";
import { RequestStatusPill } from "../../components/pills";
import { Card, ErrorBox, Kpi, Loading, Meter, PageHeader, Q } from "../../components/ui";
import { IconAlert, IconCheck, IconLock } from "../../components/icons";
import { ago, dateTime, pct, tokens, usd } from "../../lib/format";
import { requestWhat } from "../console/Requests";

export const useMe = () => useQuery({ queryKey: ["me", "summary"], queryFn: me.summary, refetchInterval: 10_000 });

export function StatusBanner({ s }: { s: MeSummary["status"] }) {
  if (s.status === "quarantined" || s.status === "revoked") {
    return (
      <div className="flex gap-3 rounded-xl bg-bad/10 px-4 py-3.5 ring-1 ring-inset ring-bad/30">
        <IconLock className="mt-0.5 shrink-0 text-bad" size={18} />
        <div className="min-w-0">
          <div className="font-semibold text-ink">
            Your AI access is {s.status === "quarantined" ? "paused (quarantined)" : "revoked"}
          </div>
          <div className="mt-0.5 text-sm text-ink2">{s.reason || "No reason was recorded."}</div>
          <div className="mt-1 text-xs text-muted">
            by {s.by || "an administrator"}
            {s.since ? ` · ${ago(s.since)} (${dateTime(s.since)})` : ""} · If this is a mistake, talk to your security team.
          </div>
        </div>
      </div>
    );
  }
  if (s.budget_scale < 1) {
    return (
      <div className="flex gap-3 rounded-xl bg-warn/10 px-4 py-3.5 ring-1 ring-inset ring-warn/30">
        <IconAlert className="mt-0.5 shrink-0 text-warn" size={18} />
        <div className="min-w-0">
          <div className="font-semibold text-ink">Limited: your daily budget is at {pct(s.budget_scale, 0)} of normal</div>
          <div className="mt-0.5 text-sm text-ink2">{s.reason || "No reason was recorded."}</div>
          <div className="mt-1 text-xs text-muted">
            by {s.by || "an administrator"}
            {s.since ? ` · ${ago(s.since)}` : ""}
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-3 rounded-xl bg-good/10 px-4 py-3 ring-1 ring-inset ring-good/25">
      <IconCheck className="shrink-0 text-good" size={18} />
      <div className="text-sm">
        <span className="font-semibold text-ink">Active.</span>{" "}
        <span className="text-ink2">
          Full access with your normal budget
          {s.budget_scale > 1 ? ` (raised to ${pct(s.budget_scale, 0)})` : ""}.
        </span>
      </div>
    </div>
  );
}

function BudgetBars({ b }: { b: BudgetScope }) {
  return (
    <div className="space-y-2.5">
      <div className="flex items-baseline justify-between">
        <span className="text-sm font-medium text-ink">{b.scope === "principal" ? "You" : `Team ${b.key}`}</span>
        <span className="text-[11px] text-muted">today</span>
      </div>
      {b.usd_limit !== null && (
        <div>
          <div className="mb-1 flex justify-between text-xs">
            <span className="text-ink2">Spend</span>
            <span className="tnum text-ink">
              {usd(b.usd)} <span className="text-muted">/ {usd(b.usd_limit)}</span>
            </span>
          </div>
          <Meter value={b.usd} max={b.usd_limit} />
        </div>
      )}
      {b.tokens_limit !== null && (
        <div>
          <div className="mb-1 flex justify-between text-xs">
            <span className="text-ink2">Tokens</span>
            <span className="tnum text-ink">
              {tokens(b.tokens)} <span className="text-muted">/ {tokens(b.tokens_limit)}</span>
            </span>
          </div>
          <Meter value={b.tokens} max={b.tokens_limit} />
        </div>
      )}
      {b.usd_limit === null && b.tokens_limit === null && <div className="text-xs text-muted">No limit set · {usd(b.usd)} today</div>}
    </div>
  );
}

export function PortalHome() {
  const qc = useQueryClient();
  const q = useMe();
  const ts = useQuery({ queryKey: ["me", "timeseries", 30], queryFn: () => me.timeseries("workflow", 30), refetchInterval: 30_000 });
  if (q.isPending) return <Loading rows={8} />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const s = q.data;
  const pending = s.requests.filter((r) => r.status === "pending");
  const runsWeek = s.runs.length;

  return (
    <div className="space-y-4">
      <PageHeader title={`Hi, ${s.principal}`} subtitle={`${s.team} · ${s.role}. Your AI usage, budget and what's running in your name.`} />
      <StatusBanner s={s.status} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="Spend today" value={usd(s.spend.today)} />
        <Kpi label="Month to date" value={usd(s.spend.month_to_date)} />
        <Kpi label="Tasks this week" value={runsWeek >= 30 ? "30+" : runsWeek} sub={`${s.by_workflow.length} workflows`} to="/portal/activity" />
        <Kpi label="Running now" value={s.leases.length} tone={s.leases.some((l) => l.flags.length) ? "warn" : undefined} sub={s.leases.length ? "see below" : "nothing running"} />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Your spend, 30 days" subtitle="By workflow" className="xl:col-span-2">
          <Q q={ts} rows={6}>
            {(d) => <StackedChart ts={d} height={220} />}
          </Q>
        </Card>
        <Card title="Budgets" subtitle={s.status.budget_scale !== 1 ? `scaled to ${pct(s.status.budget_scale, 0)}` : "Daily limits"}>
          {s.budgets.length === 0 ? (
            <div className="text-sm text-muted">No budgets apply to you.</div>
          ) : (
            <div className="space-y-5">
              {[...s.budgets]
                .sort((a, b) => Number(b.scope === "principal") - Number(a.scope === "principal"))
                .map((b) => (
                  <BudgetBars key={`${b.scope}-${b.key}`} b={b} />
                ))}
              <Link to="/portal/menu" className="block text-xs font-medium text-accent hover:underline">
                Need more? Request a budget increase →
              </Link>
            </div>
          )}
        </Card>
      </div>

      {s.tips.length > 0 && (
        <Card title="Tips to save money">
          <ul className="space-y-2">
            {s.tips.map((t, i) => (
              <li key={i} className="flex gap-2 text-sm text-ink2">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                {t}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card title="Running in your name" subtitle="Simulators, VMs and other leased resources. Stop what you're done with." flush>
        <LeasesTable
          leases={s.leases}
          showPerson={false}
          empty="Nothing running"
          onStop={async (id) => {
            const r = await me.releaseLease(id);
            await qc.invalidateQueries({ queryKey: ["me"] });
            return r;
          }}
        />
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Recent admin activity about you" flush actions={<Link to="/portal/privacy" className="text-xs font-medium text-accent hover:underline">All →</Link>}>
          <AdminLog actions={s.admin_activity.slice(0, 6)} showTarget={false} empty="No admin has acted on your account" />
        </Card>
        <Card title="Your requests" flush>
          {s.requests.length === 0 ? (
            <div className="px-4 py-6 text-center text-sm text-muted">No requests yet.</div>
          ) : (
            <ul className="divide-y divide-line/60">
              {s.requests.slice(0, 6).map((r) => (
                <li key={r.id} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
                  <div className="min-w-0">
                    <div className="truncate font-mono text-xs text-ink">{requestWhat(r)}</div>
                    <div className="truncate text-[11px] text-muted">
                      {r.note ? `“${r.note}” — ${r.decided_by}` : `asked ${ago(r.ts)}`}
                    </div>
                  </div>
                  <RequestStatusPill status={r.status} />
                </li>
              ))}
            </ul>
          )}
          {pending.length > 0 && <div className="border-t border-line px-4 py-2 text-[11px] text-muted">{pending.length} waiting for an admin</div>}
        </Card>
      </div>
    </div>
  );
}
