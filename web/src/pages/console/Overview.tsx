import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { admin } from "../../api";
import { ActivityFeed } from "../../components/ActivityFeed";
import { AdherenceBars, BarList, Sparkline, StackedChart } from "../../components/charts";
import { IconAlert, IconBox, IconGauge, IconShield, IconTerminal, IconUsers } from "../../components/icons";
import { Card, ErrorBox, Kpi, Meter, PageHeader, Q, Segmented, Skeleton } from "../../components/ui";
import { num, pct, tokens, usd } from "../../lib/format";
import { useCcProductivity } from "../../lib/productivity";

type SpendBy = "workflow" | "team" | "source" | "principal" | "model";
type AdhBy = "team" | "workflow" | "source";

function daysInMonth(d = new Date()) {
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate();
}

function Kpis() {
  const ov = useQuery({ queryKey: ["admin", "overview"], queryFn: admin.overview, refetchInterval: 10_000 });
  const adh = useQuery({ queryKey: ["admin", "adherence", "team", 30], queryFn: () => admin.adherence("team", 30), refetchInterval: 30_000 });
  const adhDaily = useQuery({ queryKey: ["admin", "adherence", "day", 30], queryFn: () => admin.adherence("day", 30), refetchInterval: 60_000 });
  if (ov.isError) return <ErrorBox error={ov.error} retry={() => ov.refetch()} />;
  if (!ov.data)
    return (
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-[104px] rounded-xl" />
        ))}
      </div>
    );
  const o = ov.data;
  const daily = o.global_usd_per_day;
  const monthly = daily ? daily * daysInMonth() : null;
  const overall = adh.data?.overall;
  const forecastRatio = monthly ? o.spend.month_forecast / monthly : 0;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <Kpi
        label="Spend today"
        icon={<IconGauge />}
        value={usd(o.spend.today)}
        sub={
          daily ? (
            <div className="space-y-1.5">
              <Meter value={o.spend.today} max={daily} />
              <span>
                of {usd(daily)}/day global budget · {pct(o.spend.today / daily, 0)}
              </span>
            </div>
          ) : (
            "no global daily budget"
          )
        }
      />
      <Kpi
        label="Month to date"
        value={usd(o.spend.month_to_date)}
        sub={
          <div className="space-y-1.5">
            {monthly ? <Meter value={o.spend.month_forecast} max={monthly} /> : null}
            <span>
              forecast <span className={forecastRatio > 1 ? "font-semibold text-bad" : "text-ink2"}>{usd(o.spend.month_forecast)}</span>
              {monthly ? ` of ${usd(monthly, { compact: true })} cap` : ""}
            </span>
          </div>
        }
        tone={forecastRatio > 1 ? "bad" : undefined}
      />
      <Kpi
        label="Policy adherence"
        icon={<IconShield />}
        tone={overall && (overall.adherence ?? 1) < 0.95 ? "warn" : "good"}
        value={overall ? pct(overall.adherence) : adh.isPending ? "…" : "—"}
        sub={
          overall ? (
            <div>
              {adhDaily.data && adhDaily.data.rows.length > 1 && (
                <div className="-mx-1 mb-1" title="Daily adherence, last 30 days">
                  <Sparkline values={adhDaily.data.rows.map((r) => (r.total ? (r.adherence ?? 1) : 1))} color="var(--s3)" height={20} min={0.9} />
                </div>
              )}
              {num(overall.total)} checks · {num(overall.block)} blocked (30d)
            </div>
          ) : (
            "no policy checks yet"
          )
        }
      />
      <Kpi
        label="People at risk"
        icon={<IconUsers />}
        tone={o.at_risk ? "warn" : undefined}
        value={o.at_risk}
        sub={o.at_risk ? "risk score past the alert line" : "nobody past the alert line"}
        to="/console/people"
      />
      <Kpi
        label="Open incidents"
        icon={<IconAlert />}
        tone={o.incidents_open ? "bad" : undefined}
        value={o.incidents_open}
        sub={`${o.requests_pending} request${o.requests_pending === 1 ? "" : "s"} awaiting approval`}
        to="/console/security"
      />
      <Kpi
        label="Running resources"
        icon={<IconBox />}
        tone={o.zombies ? "warn" : undefined}
        value={
          <span>
            {o.leases_open}
            {o.zombies > 0 && <span className="ml-2 text-sm font-medium text-warn">{o.zombies} zombie</span>}
          </span>
        }
        sub={`${usd(o.leases_running_usd)} accrued · ${tokens(o.guard_tokens_today)} guard tokens today`}
        to="/console/resources"
      />
    </div>
  );
}

function SpendCard() {
  const [by, setBy] = useState<SpendBy>("workflow");
  const [kind, setKind] = useState<"bar" | "area">("bar");
  const ts = useQuery({ queryKey: ["admin", "timeseries", by, 30], queryFn: () => admin.timeseries(by, 30), refetchInterval: 30_000 });
  const total = ts.data?.totals.reduce((a, b) => a + b, 0);
  return (
    <Card
      title="Spend, last 30 days"
      subtitle={total !== undefined ? `${usd(total)} total across every source` : "Daily cost, stacked"}
      actions={
        <>
          <Segmented<SpendBy>
            value={by}
            onChange={setBy}
            options={[
              { value: "workflow", label: "Workflow" },
              { value: "team", label: "Team" },
              { value: "source", label: "Source" },
              { value: "model", label: "Model" },
            ]}
          />
          <Segmented
            value={kind}
            onChange={setKind}
            options={[
              { value: "bar", label: "Bars" },
              { value: "area", label: "Area" },
            ]}
          />
        </>
      }
      className="xl:col-span-2"
    >
      <Q q={ts} rows={8}>
        {(d) => <StackedChart ts={d} kind={kind} />}
      </Q>
    </Card>
  );
}

function AdherenceCard() {
  const [by, setBy] = useState<AdhBy>("team");
  const q = useQuery({ queryKey: ["admin", "adherence", by, 30], queryFn: () => admin.adherence(by, 30), refetchInterval: 30_000 });
  return (
    <Card
      title="Policy adherence"
      subtitle="Share of checks that needed no intervention, worst first"
      actions={
        <Segmented<AdhBy>
          value={by}
          onChange={setBy}
          options={[
            { value: "team", label: "Team" },
            { value: "workflow", label: "Workflow" },
            { value: "source", label: "Source" },
          ]}
        />
      }
    >
      <Q q={q} rows={6}>
        {(d) => (
          <div className="max-h-[330px] overflow-y-auto pr-1">
            <AdherenceBars rows={d.rows} />
          </div>
        )}
      </Q>
    </Card>
  );
}

function TeamSpendCard() {
  const ov = useQuery({ queryKey: ["admin", "overview"], queryFn: admin.overview, refetchInterval: 10_000 });
  return (
    <Card title="Spend by team" subtitle="Month to date, with the month's forecast">
      <Q q={ov} rows={4}>
        {(o) => (
          <BarList
            rows={Object.entries(o.teams)
              .map(([k, v]) => ({ key: k, value: v.month_to_date, sub: `today ${usd(v.today)} · forecast ${usd(v.month_forecast)}` }))
              .sort((a, b) => b.value - a.value)}
            fmt={(v) => usd(v)}
            empty="No teams configured"
          />
        )}
      </Q>
    </Card>
  );
}

function ClaudeCodeCard() {
  const q = useCcProductivity(30);
  return (
    <Card
      title={
        <span className="flex items-center gap-1.5">
          <span className="text-cc">
            <IconTerminal size={15} />
          </span>
          Claude Code: cost per unit of work
        </span>
      }
      subtitle="Telemetry spend set against commits, PRs and lines (30 days)"
    >
      <Q q={q} rows={3}>
        {(d) =>
          d.usd === 0 && d.commits === 0 ? (
            <div className="py-4 text-center text-sm text-muted">No Claude Code telemetry yet.</div>
          ) : (
            <div className="grid grid-cols-2 gap-4">
              <div>
                <div className="text-[11px] uppercase tracking-wide text-muted">Cost per commit</div>
                <div className="mt-0.5 text-xl font-semibold text-ink">{d.costPerCommit !== null ? usd(d.costPerCommit) : "—"}</div>
                <div className="text-[11px] text-muted">{num(d.commits)} commits</div>
              </div>
              <div>
                <div className="text-[11px] uppercase tracking-wide text-muted">Cost per PR</div>
                <div className="mt-0.5 text-xl font-semibold text-ink">{d.costPerPr !== null ? usd(d.costPerPr) : "—"}</div>
                <div className="text-[11px] text-muted">{num(d.prs)} pull requests</div>
              </div>
              <div>
                <div className="text-[11px] uppercase tracking-wide text-muted">Lines per $1</div>
                <div className="mt-0.5 text-xl font-semibold text-ink">{d.linesPerUsd !== null ? num(d.linesPerUsd) : "—"}</div>
                <div className="text-[11px] text-muted">{num(d.linesAdded)} lines added</div>
              </div>
              <div>
                <div className="text-[11px] uppercase tracking-wide text-muted">Claude Code spend</div>
                <div className="mt-0.5 text-xl font-semibold text-ink">{usd(d.usd)}</div>
                <div className="text-[11px] text-muted">from OTLP telemetry</div>
              </div>
            </div>
          )
        }
      </Q>
    </Card>
  );
}

export function Overview() {
  return (
    <div>
      <PageHeader
        title="Overview"
        subtitle="What every person and agent does with AI, what it costs, and whether it stays inside policy."
      />
      <div className="space-y-4">
        <Kpis />
        <div className="grid gap-4 xl:grid-cols-3">
          <SpendCard />
          <AdherenceCard />
        </div>
        <div className="grid gap-4 xl:grid-cols-3">
          <Card title="Live activity" subtitle="Every check, lease, meter report and detection, newest first" className="xl:col-span-2" flush>
            <ActivityFeed load={admin.activity} queryKey={["admin", "activity"]} linkPeople maxH="520px" />
          </Card>
          <div className="space-y-4">
            <TeamSpendCard />
            <ClaudeCodeCard />
          </div>
        </div>
      </div>
    </div>
  );
}
