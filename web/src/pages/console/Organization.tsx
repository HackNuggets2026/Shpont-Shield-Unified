import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { org, orgPath, type TeamSort, type UnitRow } from "../../orgApi";
import { IconSearch } from "../../components/icons";
import { AdherenceValue, AlertCount, DeptDot, Delta, OutliersList, Pager, TeamTreemap, UnitTable, useDeptColors, useOrg, type MapColor, type UnitSortKey } from "../../components/org";
import { Button, Card, ErrorBox, PageHeader, Q, Segmented, Select, Skeleton } from "../../components/ui";
import { count, money, pctAuto, unitMoney } from "../../lib/format";
import { WINDOWS } from "./Overview";

const PAGE = 25;

/** Table sort keys the teams endpoint can sort by on the server. */
const SERVER_SORT: Partial<Record<UnitSortKey, TeamSort>> = {
  usd: "usd",
  usd_per_active: "usd_per_active",
  adherence: "adherence",
  risk: "risk",
  headcount: "headcount",
};

function DepartmentTiles({ rows, colors }: { rows: UnitRow[]; colors: Record<string, string> }) {
  const sorted = [...rows].sort((a, b) => b.usd - a.usd);
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6">
      {sorted.map((d) => (
        <Link
          key={d.name}
          to={orgPath.department(d.name)}
          className="group soft-card rounded-2xl p-3.5 transition hover:-translate-y-0.5 hover:shadow-lg"
        >
          <div className="flex items-center gap-2">
            <DeptDot color={colors[d.name]} />
            <span className="truncate text-sm font-semibold text-ink group-hover:text-accent">{d.name}</span>
          </div>
          <div className="mt-0.5 text-[11px] text-muted">
            {count(d.headcount)} people · {d.teams ?? "—"} teams
          </div>
          <div className="mt-2 flex items-baseline justify-between gap-2">
            <span className="tnum text-xl font-semibold tracking-tight text-ink">{money(d.usd)}</span>
            <Delta cur={d.usd} prev={d.usd_prev} />
          </div>
          <div className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
            <div>
              <div className="text-muted">$/active</div>
              <div className="tnum font-medium text-ink">{unitMoney(d.usd_per_active)}</div>
            </div>
            <div>
              <div className="text-muted">Adherence</div>
              <AdherenceValue v={d.adherence} className="font-medium" />
            </div>
            <div>
              <div className="text-muted">Incidents</div>
              <AlertCount n={d.incidents_open} />
            </div>
          </div>
        </Link>
      ))}
    </div>
  );
}

export function Organization() {
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const department = params.get("department") ?? "";
  const [w, setW] = useState("30");
  const days = Number(w);
  const [mapMode, setMapMode] = useState<MapColor>("growth");
  const [sort, setSort] = useState<UnitSortKey>("usd");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [offset, setOffset] = useState(0);
  const [text, setText] = useState("");
  const colors = useDeptColors();
  const o = useOrg(days);

  const map = useQuery({
    queryKey: ["admin", "org", "teams", "map", days, department],
    queryFn: () => org.teams({ days, department: department || undefined, sort: "usd", limit: 200 }),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  });
  const table = useQuery({
    queryKey: ["admin", "org", "teams", "table", days, department, sort, order, offset],
    queryFn: () => org.teams({ days, department: department || undefined, sort: SERVER_SORT[sort] ?? "usd", order, limit: PAGE, offset }),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  });

  const outliers = useQuery({
    queryKey: ["admin", "outliers", Math.min(days, 30), department],
    queryFn: () => org.outliers({ days: Math.min(days, 30), limit: 10, department: department || undefined }),
    refetchInterval: 60_000,
  });
  useEffect(() => {
    if (window.location.hash === "#outliers") document.getElementById("outliers")?.scrollIntoView();
  }, []);
  const setDept = (d: string) => {
    const n = new URLSearchParams(params);
    if (d) n.set("department", d);
    else n.delete("department");
    setParams(n, { replace: true });
    setOffset(0);
  };
  const onSort = (k: UnitSortKey) => {
    if (!SERVER_SORT[k]) return;
    if (k === sort) setOrder((x) => (x === "asc" ? "desc" : "asc"));
    else {
      setSort(k);
      setOrder(k === "adherence" ? "asc" : "desc");
    }
    setOffset(0);
  };
  const deptOptions = [{ value: "", label: "All departments" }, ...(o.data?.departments ?? []).map((d) => ({ value: d.name, label: d.name }))];

  return (
    <div>
      <PageHeader
        title="Organization"
        subtitle={
          o.data ? (
            <span className="tnum">
              {o.data.name} · {count(o.data.departments_count)} departments · {count(o.data.teams)} teams · {count(o.data.headcount)} people
            </span>
          ) : (
            "Departments, teams and the people in them"
          )
        }
        actions={<Segmented value={w} onChange={setW} options={WINDOWS} />}
      />
      <div className="space-y-4">
        <form
          className="flex flex-wrap items-center gap-2 soft-card rounded-2xl p-3"
          onSubmit={(e) => {
            e.preventDefault();
            nav(orgPath.people({ q: text.trim() || undefined, department: department || undefined }));
          }}
        >
          <label className="flex min-w-[220px] flex-1 items-center gap-2 rounded-md border border-line bg-page px-2.5 focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/30">
            <IconSearch size={15} className="text-muted" />
            <input
              className="h-9 w-full bg-transparent text-sm text-ink placeholder:text-muted focus:outline-none"
              placeholder={`Find a person by name, email or id${department ? ` in ${department}` : ""}`}
              value={text}
              onChange={(e) => setText(e.target.value)}
              aria-label="Find a person"
            />
          </label>
          <Button type="submit" variant="primary">
            Search people
          </Button>
          <div className="flex flex-wrap gap-1.5 text-xs">
            <Link to={orgPath.people({ sort: "risk" })} className="rounded-md px-2 py-1 text-ink2 ring-1 ring-inset ring-line hover:bg-raised">
              Highest risk
            </Link>
            <Link to={orgPath.people({ sort: "usd" })} className="rounded-md px-2 py-1 text-ink2 ring-1 ring-inset ring-line hover:bg-raised">
              Top spenders
            </Link>
            <Link to={orgPath.people({ status: "restricted" })} className="rounded-md px-2 py-1 text-ink2 ring-1 ring-inset ring-line hover:bg-raised">
              Restricted
            </Link>
            <Link to={orgPath.people({ status: "limited" })} className="rounded-md px-2 py-1 text-ink2 ring-1 ring-inset ring-line hover:bg-raised">
              Limited
            </Link>
          </div>
        </form>

        {o.isError ? (
          <ErrorBox error={o.error} retry={() => o.refetch()} />
        ) : o.data ? (
          <DepartmentTiles rows={o.data.departments} colors={colors} />
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6">
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} className="h-[132px] rounded-xl" />
            ))}
          </div>
        )}

        <div className="grid gap-4 xl:grid-cols-3">
        <Card
          className="xl:col-span-2"
          title="Teams at a glance"
          subtitle={`Area is spend over ${days} days; color shows ${mapMode === "growth" ? "growth against the previous window" : "adherence against the org"}. Click a team to open it.`}
          actions={
            <>
              <Select label="Department" value={department} onChange={setDept} options={deptOptions} />
              <Segmented<MapColor>
                value={mapMode}
                onChange={setMapMode}
                options={[
                  { value: "growth", label: "Growth" },
                  { value: "adherence", label: "Adherence" },
                ]}
              />
            </>
          }
        >
          <Q q={map} rows={8}>
            {(d) => (
              <>
                <TeamTreemap teams={d.rows} mode={mapMode} orgAdherence={o.data?.totals.adherence ?? null} height={420} />
                {d.total > d.rows.length && (
                  <div className="mt-1 text-[11px] text-muted">
                    Showing the {d.rows.length} highest-spend teams of {count(d.total)}.
                  </div>
                )}
              </>
            )}
          </Q>
        </Card>
        <div id="outliers" className="scroll-mt-4">
          <Card title="Outliers" subtitle={department ? `People in ${department}` : "Individuals worth a look, across the org"} flush className="h-full">
            <Q q={outliers} rows={8}>
              {(d) => <OutliersList cost={d.cost} risk={d.risk} growth={d.growth} days={Math.min(days, 30)} limit={8} />}
            </Q>
          </Card>
        </div>
        </div>

        <Card
          title={department ? `Teams in ${department}` : "All teams"}
          subtitle={table.data ? `${count(table.data.total)} teams · sorted on the server · ${PAGE} per page` : undefined}
          actions={<Select label="Department" value={department} onChange={setDept} options={deptOptions} />}
          flush
        >
          <Q q={table} rows={10}>
            {(d) => (
              <>
                <UnitTable
                  rows={d.rows}
                  kind="team"
                  colors={colors}
                  showDepartment={!department}
                  sort={sort}
                  order={order}
                  onSort={onSort}
                  sortable={Object.keys(SERVER_SORT) as UnitSortKey[]}
                  compact
                  empty="No teams"
                />
                <Pager offset={offset} limit={PAGE} total={d.total} onChange={setOffset} />
              </>
            )}
          </Q>
        </Card>
        {o.data && (
          <p className="text-xs text-muted">
            Org adherence {pctAuto(o.data.totals.adherence)} · {count(o.data.totals.interventions)} interventions in {days} days. Teams not listed in any
            department roll up under “Unassigned”.
          </p>
        )}
      </div>
    </div>
  );
}
