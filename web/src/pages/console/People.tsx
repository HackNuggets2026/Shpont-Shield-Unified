import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { org, orgPath, type Order, type PeopleQuery, type PeopleSort, type PersonRow } from "../../orgApi";
import { LevelPill, PersonStatusPill, levelTone } from "../../components/pills";
import { Breadcrumbs, Pager, SortTh, useDebounced, useOrg } from "../../components/org";
import { IconSearch } from "../../components/icons";
import { Card, Empty, ErrorBox, Loading, PageHeader, Pill, Select, TableWrap, cx } from "../../components/ui";
import { count, money, pct, tokens, usd } from "../../lib/format";

/** A risk score, coloured by the insider-risk thresholds (watch 30, restricted 120 by default). */
export function RiskBar({ score, watch = 30, restricted = 120 }: { score: number; watch?: number; restricted?: number }) {
  const tone = score >= restricted ? "bg-bad" : score >= watch ? "bg-warn" : "bg-good/70";
  return (
    <div className="flex items-center gap-2">
      <span className="tnum w-9 text-right text-sm font-semibold text-ink">{Math.round(score)}</span>
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-ink/10">
        <div className={cx("h-full rounded-full", tone)} style={{ width: `${(Math.min(score, restricted) / restricted) * 100}%` }} />
      </div>
    </div>
  );
}

export const PEOPLE_PAGE = 50;

/** One server page of people. Never more than `limit` rows on screen. */
export function PeopleTable({
  rows,
  days,
  sort,
  order,
  onSort,
  showTeam = true,
}: {
  rows: PersonRow[];
  days: number;
  sort: PeopleSort;
  order: Order;
  onSort: (k: PeopleSort) => void;
  showTeam?: boolean;
}) {
  const nav = useNavigate();
  if (!rows.length) return <Empty title="Nobody matches" hint="Loosen the filters or search by another part of the name or email." />;
  return (
    <TableWrap>
      <table className="tbl min-w-[960px]">
        <thead>
          <tr>
            <SortTh<PeopleSort> k="name" sort={sort} order={order} onSort={onSort}>
              Person
            </SortTh>
            {showTeam && <th>Team</th>}
            <SortTh<PeopleSort> k="risk" sort={sort} order={order} onSort={onSort}>
              Risk
            </SortTh>
            <th>Status</th>
            <SortTh<PeopleSort> k="usd" sort={sort} order={order} onSort={onSort} right>
              Spend {days}d
            </SortTh>
            <SortTh<PeopleSort> k="tokens" sort={sort} order={order} onSort={onSort} right>
              Tokens {days}d
            </SortTh>
            <th className="text-right">Today</th>
            <th className="text-right">Budget</th>
            <th className="text-right">Incidents</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.principal} className={cx("row-link", levelTone(r.level) === "bad" && "bg-bad/[0.04]")} onClick={() => nav(orgPath.person(r.principal))}>
              <td className="max-w-[260px]">
                <div className="truncate font-medium text-ink">{r.name || r.principal}</div>
                <div className="truncate text-xs text-muted">
                  {r.principal}
                  {r.title || r.role ? ` · ${r.title || r.role}` : ""}
                </div>
              </td>
              {showTeam && (
                <td className="max-w-[200px]">
                  <div className="truncate text-ink2">{r.team}</div>
                  <div className="truncate text-[11px] text-muted">{r.department}</div>
                </td>
              )}
              <td>
                <div className="flex items-center gap-2">
                  <RiskBar score={r.risk} />
                  <LevelPill level={r.level} />
                </div>
              </td>
              <td>
                <PersonStatusPill status={r.status} scale={r.budget_scale} />
              </td>
              <td className="tnum text-right font-medium text-ink">{money(r.usd)}</td>
              <td className="tnum text-right">{tokens(r.tokens)}</td>
              <td className="tnum text-right text-ink2">{usd(r.today?.usd ?? 0)}</td>
              <td className="tnum text-right">{pct(r.budget_scale, 0)}</td>
              <td className="text-right">{r.open_incidents ? <Pill tone="bad">{r.open_incidents} open</Pill> : <span className="text-muted">0</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

/** A server-paginated people query with sort state, shared by the search page and the team page. */
export function usePeoplePage(base: PeopleQuery, initialSort: PeopleSort = "risk") {
  const [sort, setSort] = useState<PeopleSort>(initialSort);
  const [order, setOrder] = useState<Order>(initialSort === "name" ? "asc" : "desc");
  const [offset, setOffset] = useState(0);
  const key = JSON.stringify(base);
  useEffect(() => setOffset(0), [key]);
  const q = useQuery({
    queryKey: ["admin", "people-search", base, sort, order, offset],
    queryFn: () => org.people({ ...base, sort, order, limit: PEOPLE_PAGE, offset }),
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  });
  const onSort = (k: PeopleSort) => {
    if (k === sort) setOrder((o) => (o === "asc" ? "desc" : "asc"));
    else {
      setSort(k);
      setOrder(k === "name" ? "asc" : "desc");
    }
    setOffset(0);
  };
  return { q, sort, order, onSort, offset, setOffset };
}

const STATUSES = [
  { value: "", label: "Any status" },
  { value: "active", label: "Active" },
  { value: "limited", label: "Limited budget" },
  { value: "watch", label: "Watch" },
  { value: "restricted", label: "Restricted" },
  { value: "revoked", label: "Revoked" },
];

/** /console/people?q=&department=&team=&status=&sort= — server-side search; the URL is the state. */
export function People() {
  const [params, setParams] = useSearchParams();
  const urlQ = params.get("q") ?? "";
  const department = params.get("department") ?? "";
  const team = params.get("team") ?? "";
  const status = params.get("status") ?? "";
  const sortParam = (params.get("sort") as PeopleSort | null) ?? (urlQ ? "name" : "risk");
  const [text, setText] = useState(urlQ);
  const debounced = useDebounced(text.trim(), 250);
  useEffect(() => setText(urlQ), [urlQ]);

  const set = (k: string, v: string) => {
    const n = new URLSearchParams(params);
    if (v) n.set(k, v);
    else n.delete(k);
    if (k === "department") n.delete("team");
    setParams(n, { replace: true });
  };
  useEffect(() => {
    if (debounced !== urlQ) set("q", debounced);
  }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps

  const o = useOrg(30);
  const teams = useQuery({
    queryKey: ["admin", "org", "teams", "names", department],
    queryFn: () => org.teams({ department: department || undefined, sort: "headcount", limit: 200 }),
    staleTime: 60_000,
  });
  const p = usePeoplePage({ q: urlQ || undefined, department: department || undefined, team: team || undefined, status: status || undefined, days: 30 }, sortParam);

  const crumbs = [{ label: "Organization", to: orgPath.root }];
  if (department) crumbs.push({ label: department, to: orgPath.department(department) });
  if (team) crumbs.push({ label: team, to: orgPath.team(team) });

  return (
    <div>
      <PageHeader
        back={<Breadcrumbs items={[...crumbs, { label: "People" }]} />}
        title="People"
        subtitle={
          p.q.data
            ? `${p.q.data.total.toLocaleString("en-US")} ${p.q.data.total === 1 ? "person matches" : "people match"}${o.data ? ` of ${count(o.data.headcount)}` : ""} · ${PEOPLE_PAGE} per page, searched on the server`
            : "Search by name, email or id; filter by department, team and status."
        }
      />
      <Card
        flush
        title={
          <label className="flex w-full min-w-[220px] items-center gap-2 rounded-md border border-line bg-page px-2.5 focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/30 sm:w-80">
            <IconSearch size={14} className="text-muted" />
            <input
              autoFocus
              className="h-8 w-full bg-transparent text-sm font-normal text-ink placeholder:text-muted focus:outline-none"
              placeholder="Name, email or id"
              value={text}
              onChange={(e) => setText(e.target.value)}
              aria-label="Search people"
            />
          </label>
        }
        actions={
          <>
            <Select
              label="Department"
              value={department}
              onChange={(v) => set("department", v)}
              options={[{ value: "", label: "All departments" }, ...(o.data?.departments ?? []).map((d) => ({ value: d.name, label: d.name }))]}
            />
            <Select
              label="Team"
              value={team}
              onChange={(v) => set("team", v)}
              options={[
                { value: "", label: department ? `All teams in ${department}` : "All teams" },
                ...[...(teams.data?.rows ?? [])].sort((a, b) => a.name.localeCompare(b.name)).map((t) => ({ value: t.name, label: t.name })),
              ]}
            />
            <Select label="Status" value={status} onChange={(v) => set("status", v)} options={STATUSES} />
          </>
        }
      >
        {p.q.isPending ? (
          <div className="p-3">
            <Loading rows={10} />
          </div>
        ) : p.q.isError ? (
          <div className="p-3">
            <ErrorBox error={p.q.error} retry={() => p.q.refetch()} />
          </div>
        ) : (
          <div className={cx(p.q.isPlaceholderData && "opacity-60 transition-opacity")}>
            <PeopleTable rows={p.q.data.rows} days={30} sort={p.sort} order={p.order} onSort={p.onSort} />
            <Pager offset={p.offset} limit={PEOPLE_PAGE} total={p.q.data.total} onChange={p.setOffset} />
          </div>
        )}
      </Card>
    </div>
  );
}
