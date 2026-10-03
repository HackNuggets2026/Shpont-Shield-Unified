import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { org, orgPath, type OrgUnit, type UnitRow } from "../../orgApi";
import { BarList, Sparkline, StackedChart } from "../../components/charts";
import { useWorkflowColors, WfName, wfLabel } from "../../lib/workflows";
import { Breadcrumbs, DeptDot, Delta, OutliersList, Pager, UnitTable, useDeptColors } from "../../components/org";
import { Card, ErrorBox, Loading, Meter, PageHeader, Pill, Q, Segmented, TableWrap, cx } from "../../components/ui";
import { count, money, pctAuto, share, unitMoney, usd } from "../../lib/format";
import { PEOPLE_PAGE, PeopleTable, usePeoplePage } from "./People";
import { WINDOWS } from "./Overview";

function Tile({ label, value, children, tone }: { label: string; value: ReactNode; children?: ReactNode; tone?: "bad" | "warn" }) {
  return (
    <div className="h-full rounded-xl border border-line bg-panel p-4 shadow-sm">
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className={cx("tnum mt-1.5 text-2xl font-semibold tracking-tight", tone === "bad" ? "text-bad" : tone === "warn" ? "text-warn" : "text-ink")}>{value}</div>
      {children && <div className="mt-1.5 space-y-1 text-xs text-muted">{children}</div>}
    </div>
  );
}

function Tiles({ m, trend, orgUsd }: { m: UnitRow; trend: (number | null)[]; orgUsd?: number }) {
  const t = trend.map((v) => v ?? 1);
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <Tile label="Spend" value={money(m.usd)}>
        <div>
          <Delta cur={m.usd} prev={m.usd_prev} /> vs previous period ({money(m.usd_prev)})
        </div>
        {orgUsd ? <div>{share(m.usd / orgUsd)} of the organization</div> : null}
      </Tile>
      <Tile label="Policy adherence" value={pctAuto(m.adherence)} tone={(m.adherence ?? 1) < 0.95 ? "warn" : undefined}>
        {t.length > 1 && (
          <div className="-mx-1" title="Daily adherence">
            <Sparkline values={t} color="var(--s3)" height={22} min={Math.min(0.9, ...t)} />
          </div>
        )}
        <div>
          {count(m.interventions)} interventions in {count(m.checks)} checks
        </div>
      </Tile>
      <Tile label="People at risk" value={count(m.people_at_risk)} tone={m.people_at_risk ? "warn" : undefined}>
        <div className={m.incidents_open ? "font-medium text-bad" : undefined}>{count(m.incidents_open)} open incidents</div>
      </Tile>
      <Tile label="Spend per active person" value={unitMoney(m.usd_per_active)}>
        <Meter value={m.active} max={m.headcount || 1} tone="accent" />
        <div>
          {count(m.active)} of {count(m.headcount)} active ({share(m.headcount ? m.active / m.headcount : null)}) · Claude Code {count(m.claude_code_users)}
        </div>
      </Tile>
    </div>
  );
}

/** One chart card; the table view of the same window (workflows with cost per run, resources) is one toggle away. */
function SpendCard({ u, days }: { u: OrgUnit; days: number }) {
  const [view, setView] = useState<"chart" | "workflows">("chart");
  const colors = useWorkflowColors();
  return (
    <Card
      title={`Spend by workflow, last ${days} days`}
      subtitle={`${money(u.metrics.usd)} in total`}
      actions={
        <Segmented
          value={view}
          onChange={setView}
          options={[
            { value: "chart", label: "Chart" },
            { value: "workflows", label: "Table" },
          ]}
        />
      }
    >
      {view === "chart" ? (
        <StackedChart ts={u.spend} kind="bar" compact height={260} colors={colors} labelOf={wfLabel} />
      ) : (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <TableWrap>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Workflow</th>
                  <th className="text-right">Runs</th>
                  <th className="text-right">Spend</th>
                  <th className="text-right" title="Cost per run, median and 90th percentile">
                    Cost / run p50 – p90
                  </th>
                </tr>
              </thead>
              <tbody>
                {u.by_workflow.map((w) => (
                  <tr key={w.workflow}>
                    <td>
                      <span className="flex items-center gap-2">
                        <DeptDot color={colors[w.workflow]} />
                        <WfName id={w.workflow} />
                      </span>
                    </td>
                    <td className="tnum text-right">{count(w.runs)}</td>
                    <td className="tnum text-right font-medium">{money(w.usd)}</td>
                    <td className="tnum whitespace-nowrap text-right">
                      {w.runs ? (
                        <>
                          {usd(w.p50)} <span className="text-muted">–</span> {usd(w.p90)}
                        </>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          <div>
            <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted">By resource</div>
            <BarList tone="var(--s3)" rows={u.by_resource.filter((r) => r.usd > 0).map((r) => ({ key: r.resource, value: r.usd }))} fmt={money} empty="No resource spend" />
          </div>
        </div>
      )}
    </Card>
  );
}

function TeamPeople({ team, days }: { team: string; days: number }) {
  const p = usePeoplePage({ team, days }, "usd");
  return (
    <Card
      title="People"
      subtitle={p.q.data ? `${count(p.q.data.total)} in this team · ${PEOPLE_PAGE} per page` : undefined}
      actions={
        <Link to={orgPath.people({ team })} className="text-xs font-medium text-accent hover:underline">
          Search and filter →
        </Link>
      }
      flush
    >
      {p.q.isPending ? (
        <div className="p-3">
          <Loading rows={8} />
        </div>
      ) : p.q.isError ? (
        <div className="p-3">
          <ErrorBox error={p.q.error} retry={() => p.q.refetch()} />
        </div>
      ) : (
        <div className={cx(p.q.isPlaceholderData && "opacity-60")}>
          <PeopleTable rows={p.q.data.rows} days={days} sort={p.sort} order={p.order} onSort={p.onSort} showTeam={false} />
          <Pager offset={p.offset} limit={PEOPLE_PAGE} total={p.q.data.total} onChange={p.setOffset} />
        </div>
      )}
    </Card>
  );
}

export function OrgUnitPage({ kind }: { kind: "department" | "team" }) {
  const { name = "" } = useParams();
  const [w, setW] = useState("30");
  const days = Number(w);
  const colors = useDeptColors();
  const q = useQuery({
    queryKey: ["admin", "org", "unit", kind, name, days],
    queryFn: () => org.unit(kind, name, days),
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  });
  const orgQ = useQuery({ queryKey: ["admin", "org", days], queryFn: () => org.org(days), staleTime: 30_000 });
  const filter = kind === "department" ? { department: name } : { team: name };
  const growth = useQuery({
    queryKey: ["admin", "outliers", Math.min(days, 30), kind, name],
    queryFn: () => org.outliers({ days: Math.min(days, 30), limit: 10, ...filter }),
    refetchInterval: 60_000,
  });

  const u = q.data;
  const dept = kind === "department" ? name : (u?.department ?? u?.metrics.department ?? "");
  const crumbs: { label: ReactNode; to?: string }[] = [{ label: "Organization", to: orgPath.root }];
  if (kind === "team" && dept) crumbs.push({ label: dept, to: orgPath.department(dept) });
  crumbs.push({ label: name });

  return (
    <div className="space-y-5">
      <PageHeader
        back={<Breadcrumbs items={crumbs} />}
        title={
          <span className="flex items-center gap-2">
            {name}
            <Pill tone="neutral">{kind}</Pill>
          </span>
        }
        subtitle={
          u ? (
            <span className="tnum">
              {[
                kind === "department" && u.metrics.owner ? `owner ${u.metrics.owner}` : null,
                kind === "department" ? `${count(u.teams.length || u.metrics.teams || 0)} teams` : null,
                `${count(u.metrics.headcount)} people`,
                `${count(u.metrics.active)} active`,
                u.metrics.top_workflow ? `top workflow ${u.metrics.top_workflow}` : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
          ) : undefined
        }
        actions={
          <>
            <Link to={orgPath.people(filter)} className="text-xs font-medium text-accent hover:underline">
              Search people here
            </Link>
            <Segmented value={w} onChange={setW} options={WINDOWS} />
          </>
        }
      />
      <Q q={q} rows={10}>
        {(u) => (
          <div className={cx("space-y-5", q.isPlaceholderData && "opacity-60")}>
            <Tiles m={u.metrics} trend={u.adherence_trend.values} orgUsd={orgQ.data?.totals.usd} />
            <div className="grid gap-4 xl:grid-cols-3">
              <div className="xl:col-span-2">
                <SpendCard u={u} days={days} />
              </div>
              <Card title="Outliers" subtitle={`Individuals in this ${kind} worth a look`} flush className="h-full">
                <OutliersList
                  cost={growth.data?.cost ?? u.outliers.cost}
                  risk={growth.data?.risk ?? u.outliers.risk}
                  growth={growth.data?.growth}
                  days={Math.min(days, 30)}
                  limit={8}
                />
              </Card>
            </div>
            {kind === "department" ? (
              <Card title="Teams" subtitle="Click a team for its people" flush>
                <UnitTable rows={u.teams} kind="team" colors={colors} compact empty="No teams in this department" />
              </Card>
            ) : (
              <TeamPeople team={name} days={days} />
            )}
          </div>
        )}
      </Q>
    </div>
  );
}
