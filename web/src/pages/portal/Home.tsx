import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { me, type BudgetScope } from "../../api";
import { StackedChart } from "../../components/charts";
import { Card, ErrorBox, Kpi, Loading, Meter, PageHeader, Q, Segmented } from "../../components/ui";
import { AccessStatus, LeaseList, MyAdminLog, RequestList, exhausted, inHours, untilUtcMidnight } from "../../components/portal/widgets";
import { tokens, usd } from "../../lib/format";

export const useMe = () => useQuery({ queryKey: ["me", "summary"], queryFn: me.summary, refetchInterval: 10_000 });

function Bar({ label, used, limit, fmt }: { label: string; used: number; limit: number | null; fmt: (v: number) => string }) {
  return (
    <div>
      <div className="mb-1 flex justify-between gap-2 text-xs">
        <span className="text-ink2">{label}</span>
        <span className="tnum text-ink">
          {fmt(used)} {limit !== null ? <span className="text-muted">of {fmt(limit)}</span> : <span className="text-muted">· no limit</span>}
        </span>
      </div>
      {limit !== null && <Meter value={used} max={limit} />}
    </div>
  );
}

function BudgetBlock({ b }: { b: BudgetScope }) {
  const over = exhausted(b);
  return (
    <div className="space-y-2.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium text-ink">{b.scope === "principal" ? "Your daily budget" : `Team ${b.key} (shared)`}</span>
        {over && <span className="text-[11px] font-medium text-warn">used up</span>}
      </div>
      {b.usd_limit === null && b.tokens_limit === null ? (
        <div className="text-xs text-muted">No limit set · {usd(b.usd)} used today</div>
      ) : (
        <>
          <Bar label="Spend" used={b.usd} limit={b.usd_limit} fmt={(v) => usd(v)} />
          <Bar label="Tokens" used={b.tokens} limit={b.tokens_limit} fmt={tokens} />
        </>
      )}
    </div>
  );
}

/** Month-to-date extrapolated to the whole month (UTC). */
function monthPace(mtd: number): number | null {
  const d = new Date();
  const start = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
  const days = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate();
  const elapsed = (Date.now() - start) / 86_400_000;
  return elapsed >= 1 ? (mtd / elapsed) * days : null;
}

export function PortalHome() {
  const qc = useQueryClient();
  const q = useMe();
  const [by, setBy] = useState<"workflow" | "source">("workflow");
  const ts = useQuery({ queryKey: ["me", "timeseries", by, 30], queryFn: () => me.timeseries(by, 30), refetchInterval: 60_000 });
  if (q.isPending) return <Loading rows={8} />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const s = q.data;
  const pending = s.requests.filter((r) => r.status === "pending");
  const mine = s.budgets.find((b) => b.scope === "principal");
  const runningUsd = s.leases.reduce((a, l) => a + (l.running_usd ?? 0), 0);
  const idle = s.leases.filter((l) => l.flags.length > 0 || (l.idle_minutes ?? 0) >= 10).length;
  const pace = monthPace(s.spend.month_to_date);
  const budgets = [...s.budgets].sort((a, b) => Number(b.scope === "principal") - Number(a.scope === "principal"));

  return (
    <div className="space-y-4">
      <PageHeader
        title={`Hi, ${s.principal}`}
        subtitle={
          <>
            {s.team} · {s.role}. What you can use, what it costs, and what runs in your name.
          </>
        }
      />
      <AccessStatus s={s} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi
          label="Spend today"
          value={usd(s.spend.today)}
          sub={mine?.usd_limit ? `of ${usd(mine.usd_limit)} daily` : mine?.tokens_limit ? `${tokens(mine.tokens)} of ${tokens(mine.tokens_limit)} tokens` : "no daily limit"}
        />
        <Kpi label="This month" value={usd(s.spend.month_to_date)} sub={pace !== null ? `on pace for ~${usd(pace)}` : "month just started"} />
        <Kpi
          label="Running now"
          value={s.leases.length}
          tone={idle ? "warn" : undefined}
          sub={s.leases.length ? `${usd(runningUsd)} so far${idle ? ` · ${idle} idle` : ""}` : "nothing running"}
        />
        <Kpi label="Waiting on an admin" value={pending.length} sub={pending.length ? "see your requests below" : "no open requests"} to="/portal/menu" />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card
          title="Your spend, last 30 days"
          subtitle={by === "workflow" ? "By workflow" : "By where it came from"}
          className="xl:col-span-2"
          actions={
            <Segmented
              value={by}
              onChange={setBy}
              options={[
                { value: "workflow", label: "Workflow" },
                { value: "source", label: "Source" },
              ]}
            />
          }
        >
          <Q q={ts} rows={6}>
            {(d) => <StackedChart ts={d} height={220} />}
          </Q>
        </Card>
        <Card title="Today's budgets" subtitle={`Reset at 00:00 UTC, in ${inHours(untilUtcMidnight())}`}>
          {budgets.length === 0 ? (
            <div className="text-sm text-muted">No daily budgets apply to you.</div>
          ) : (
            <div className="space-y-5">
              {budgets.map((b) => (
                <BudgetBlock key={`${b.scope}-${b.key}`} b={b} />
              ))}
              <Link to="/portal/menu" className="block text-xs font-medium text-accent hover:underline">
                Need more? Ask for a bigger budget →
              </Link>
            </div>
          )}
        </Card>
      </div>

      <Card title="Running in your name" subtitle="Paid by the minute. Stop what you are done with." flush>
        <LeaseList
          leases={s.leases}
          onStop={async (id) => {
            const r = await me.releaseLease(id);
            await qc.invalidateQueries({ queryKey: ["me"] });
            return r;
          }}
        />
      </Card>

      {s.tips.length > 0 && (
        <Card title="Ways to spend less">
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

      <div className="grid gap-4 xl:grid-cols-2">
        <Card
          title="Latest admin activity about you"
          subtitle="Every change or content view, with the stated reason"
          flush
          actions={
            <Link to="/portal/privacy" className="text-xs font-medium text-accent hover:underline">
              Full record →
            </Link>
          }
        >
          <MyAdminLog actions={s.admin_activity} limit={4} />
        </Card>
        <Card
          title="Your requests"
          flush
          actions={
            <Link to="/portal/menu" className="text-xs font-medium text-accent hover:underline">
              Ask for more →
            </Link>
          }
        >
          <RequestList requests={s.requests} limit={4} />
        </Card>
      </div>
    </div>
  );
}
