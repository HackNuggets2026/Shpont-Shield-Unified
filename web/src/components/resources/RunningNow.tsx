import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { admin, type CatalogItem } from "../../api";
import { ops, type LeasesResponse, type OrgLease } from "../../opsApi";
import { countC, usdC } from "../../lib/compact";
import { LeasesTable } from "../Leases";
import { DepartmentSelect, Pager, useUrlFilters } from "../opsKit";
import { ErrorBox, Loading, Q, Segmented, Select } from "../ui";
import { LiveLeases, RecentReclaims } from "./LiveLeases";
import { RollupTable } from "./Rollups";

const PAGE = 25;

/** The summary for servers that do not send one yet: rolled up from the open list (complete there). */
function rollUp(open: OrgLease[]): NonNullable<LeasesResponse["summary"]> {
  const by = (key: (l: OrgLease) => string) => {
    const m = new Map<string, { open: number; zombies: number; running_usd: number }>();
    for (const l of open) {
      const e = m.get(key(l)) ?? { open: 0, zombies: 0, running_usd: 0 };
      e.open++;
      if (l.flags.length) e.zombies++;
      e.running_usd += l.running_usd ?? 0;
      m.set(key(l), e);
    }
    return [...m].sort((a, b) => b[1].running_usd - a[1].running_usd);
  };
  return {
    by_resource: by((l) => l.resource).map(([resource, e]) => ({ resource, ...e })),
    by_department: by((l) => l.department || l.team || "Unassigned").map(([department, e]) => ({ department, ...e })),
  };
}
const KEYS = ["department", "resource", "zombies"] as const;

/** Simulators and VMs held right now: rolled up by resource and department, then the open leases, paged. */
export function RunningNow({ catalog, leasable }: { catalog: Record<string, CatalogItem>; leasable: CatalogItem[] }) {
  const qc = useQueryClient();
  const f = useUrlFilters(KEYS, {}, "l_");
  const v = f.values;
  const zombies = v.zombies === "1";
  const all = useQuery({ queryKey: ["admin", "leases", "summary"], queryFn: () => ops.leases({ limit: 1 }), refetchInterval: 5_000 });
  const list = useQuery({
    queryKey: ["admin", "leases", "page", v, f.page],
    queryFn: () => ops.leases({ department: v.department, resource: v.resource, zombies, limit: PAGE, offset: f.page * PAGE }),
    placeholderData: keepPreviousData,
    refetchInterval: 5_000,
  });
  const title = (k: string) => catalog[k]?.title || k;
  const sum = all.data ? (all.data.summary ?? rollUp(all.data.open)) : undefined;
  const d = list.data;
  // Older servers ignore the filters: then the page is narrowed here so the table never shows the wrong rows.
  const rows = (d?.open ?? []).filter(
    (l) => (!v.resource || l.resource === v.resource) && (!v.department || !l.department || l.department === v.department) && (!zombies || l.flags.length > 0),
  );
  // A server that ignores limit/offset sends every open lease: page those here.
  const legacy = !!d && d.open_total === undefined && d.open.length > PAGE;
  const total = d?.open_total ?? (legacy ? rows.length : null);
  const shown = legacy ? rows.slice(f.page * PAGE, (f.page + 1) * PAGE) : rows;

  return (
    <div>
      <div className="grid gap-5 border-b border-line p-4 md:grid-cols-2">
        {all.isPending ? (
          <Loading rows={4} />
        ) : all.isError ? (
          <ErrorBox error={all.error} retry={() => all.refetch()} />
        ) : (
          <>
            <RollupTable
              title="By resource"
              head={["Resource", "Running", "Zombies", "Cost so far"]}
              rows={(sum?.by_resource ?? []).map((r) => ({
                key: r.resource,
                label: title(r.resource),
                cells: [{ value: r.open }, { value: r.zombies, tone: "warn" }, { value: r.running_usd, fmt: "usd" }],
              }))}
              active={v.resource}
              onPick={(k) => f.set({ resource: k })}
              empty="Nothing running"
            />
            <RollupTable
              title="By department"
              head={["Department", "Running", "Zombies", "Cost so far"]}
              rows={(sum?.by_department ?? []).map((r) => ({
                key: r.department,
                cells: [{ value: r.open }, { value: r.zombies, tone: "warn" }, { value: r.running_usd, fmt: "usd" }],
              }))}
              active={v.department}
              onPick={(k) => f.set({ department: k })}
              empty="Nothing running"
            />
          </>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2">
        <Segmented
          value={zombies ? "1" : ""}
          onChange={(x) => f.set({ zombies: x })}
          options={[
            { value: "", label: "All running" },
            { value: "1", label: "Zombies only" },
          ]}
        />
        <DepartmentSelect value={v.department} onChange={(x) => f.set({ department: x })} />
        <Select label="Resource" value={v.resource} onChange={(x) => f.set({ resource: x })} options={[{ value: "", label: "All resources" }, ...leasable.map((r) => ({ value: r.name, label: r.title || r.name }))]} />
        {f.dirty && (
          <button type="button" onClick={f.reset} className="text-xs font-medium text-accent hover:underline">
            Reset
          </button>
        )}
        <span className="ml-auto text-xs text-muted">{total !== null ? `${countC(total)} match` : ""}</span>
      </div>

      <Q q={list} rows={4}>
        {(data) => (
          <>
            <LiveLeases
              leases={shown}
              fetchedAt={list.dataUpdatedAt}
              catalog={catalog}
              onStop={async (id) => {
                const r = await admin.releaseLease(id);
                await qc.invalidateQueries({ queryKey: ["admin"] });
                return r;
              }}
            />
            <Pager page={f.page} size={PAGE} shown={shown.length} total={total} hasMore={total !== null ? (f.page + 1) * PAGE < total : data.open.length >= PAGE} onPage={f.setPage} fetching={list.isPlaceholderData} className="border-t border-line" />
            <RecentReclaims recent={data.recent} catalog={catalog} />
            {data.recent.length > 0 && (
              <details className="border-t border-line">
                <summary className="cursor-pointer px-4 py-2.5 text-xs font-medium text-ink2 hover:text-ink">
                  Recently stopped ({Math.min(data.recent.length, 10)}){" "}
                  <span className="font-normal text-muted">
                    · {usdC(data.recent.slice(0, 10).reduce((s, l) => s + (l.usd || 0), 0))} total
                  </span>
                </summary>
                <LeasesTable leases={data.recent.slice(0, 10)} closed empty="No leases have ended yet" />
              </details>
            )}
          </>
        )}
      </Q>
    </div>
  );
}
