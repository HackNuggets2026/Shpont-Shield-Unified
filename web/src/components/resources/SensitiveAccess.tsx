import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { admin, type CatalogItem } from "../../api";
import { ops } from "../../opsApi";
import { countC } from "../../lib/compact";
import { useNow } from "../../lib/useNow";
import { GrantsTable } from "../Grants";
import { DepartmentSelect, Pager, useUrlFilters } from "../opsKit";
import { SensitivityPill } from "../pills";
import { Button, ErrorBox, Loading, Q, Segmented, Select, cx } from "../ui";
import { IconKey } from "../icons";
import { grantLength } from "./catalogInfo";

const PAGE = 25;
const KEYS = ["live", "department", "resource"] as const;

function Figure({ label, value, tone }: { label: string; value: string; tone?: "warn" | "cc" }) {
  return (
    <div className="min-w-0">
      <div className={cx("tnum text-xl font-semibold tracking-tight", tone === "warn" ? "text-warn" : tone === "cc" ? "text-cc" : "text-ink")}>{value}</div>
      <div className="text-[11px] text-muted">{label}</div>
    </div>
  );
}

/** Who can touch production data, deploys or external email right now, and until when. */
export function SensitiveAccess({
  items,
  onGrant,
  onOpen,
}: {
  items: CatalogItem[];
  onGrant: (resource?: string) => void;
  onOpen: (r: CatalogItem) => void;
}) {
  const qc = useQueryClient();
  const now = useNow(30_000);
  const f = useUrlFilters(KEYS, { live: "1" }, "g_");
  const v = f.values;
  const summary = useQuery({ queryKey: ["admin", "grants", "summary"], queryFn: () => ops.grants({ live: true, limit: 1 }), refetchInterval: 10_000 });
  const list = useQuery({
    queryKey: ["admin", "grants", "page", v, f.page],
    queryFn: () => ops.grants({ live: v.live === "1", department: v.department, resource: v.resource, limit: PAGE, offset: f.page * PAGE }),
    placeholderData: keepPreviousData,
    refetchInterval: 10_000,
  });

  const s = summary.data?.summary;
  // Servers without the envelope send every grant: summarise and page those here.
  const legacy = summary.data && !s ? summary.data.rows.filter((g) => g.live) : null;
  const live = s?.live ?? legacy?.length;
  const soon = s?.expiring_1h ?? legacy?.filter((g) => g.expires && g.expires - now < 3600).length;
  const liveBy = new Map<string, number>((s?.by_resource ?? []).map((r) => [r.resource, r.live]));
  if (legacy) for (const g of legacy) liveBy.set(g.resource, (liveBy.get(g.resource) ?? 0) + 1);

  const page = list.data;
  const rows = (page?.rows ?? []).filter((g) => (v.live !== "1" || g.live) && (!v.resource || g.resource === v.resource) && (!v.department || !g.department || g.department === v.department));
  const legacyPaging = page && page.summary === null && page.total === null;
  const shown = legacyPaging ? rows.slice(f.page * PAGE, (f.page + 1) * PAGE) : rows;
  const total = legacyPaging ? rows.length : (page?.total ?? null);

  return (
    <div>
      <div className="grid gap-5 border-b border-line p-4 lg:grid-cols-[minmax(0,220px)_minmax(0,1fr)]">
        {summary.isPending ? (
          <Loading rows={3} />
        ) : summary.isError ? (
          <ErrorBox error={summary.error} retry={() => summary.refetch()} />
        ) : (
          <div className="grid grid-cols-3 gap-3 lg:grid-cols-1">
            <Figure label="live grants" value={live === undefined ? "…" : countC(live)} tone="cc" />
            <Figure label="expire within the hour" value={soon === undefined ? "…" : countC(soon)} tone={soon ? "warn" : undefined} />
            <Figure label="sensitive resources" value={countC(items.length)} />
          </div>
        )}
        <div className="min-w-0">
          <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">What needs a grant</div>
          <ul className="divide-y divide-line/60 text-xs">
            {[...items]
              .sort((a, b) => (liveBy.get(b.name) ?? 0) - (liveBy.get(a.name) ?? 0) || (a.title || a.name).localeCompare(b.title || b.name))
              .map((r) => {
                const n = liveBy.get(r.name) ?? 0;
                const active = v.resource === r.name;
                return (
                  <li key={r.name} className={cx("flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5", active && "bg-accent/[0.07]")}>
                    <button type="button" className="min-w-0 flex-1 truncate text-left font-medium text-ink hover:text-accent" onClick={() => onOpen(r)} title={r.description || "Details"}>
                      {r.title || r.name}
                    </button>
                    <SensitivityPill s={r.sensitivity} />
                    <span className="hidden text-muted sm:inline">up to {grantLength(r.grant?.max_minutes)}</span>
                    <button
                      type="button"
                      onClick={() => f.set({ resource: active ? "" : r.name, live: "1" })}
                      className={cx("tnum w-16 text-right", n ? "font-semibold text-cc hover:underline" : "text-muted")}
                      title={active ? "Show every resource" : "Show who holds it"}
                    >
                      {n ? `${countC(n)} live` : "none"}
                    </button>
                    <Button size="sm" variant="ghost" onClick={() => onGrant(r.name)} aria-label={`Grant ${r.title || r.name}`}>
                      <IconKey size={12} /> Grant
                    </Button>
                  </li>
                );
              })}
          </ul>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2">
        <Segmented
          value={v.live === "1" ? "1" : "0"}
          onChange={(x) => f.set({ live: x })}
          options={[
            { value: "1", label: "Live now" },
            { value: "0", label: "Including expired" },
          ]}
        />
        <DepartmentSelect value={v.department} onChange={(x) => f.set({ department: x })} />
        <Select label="Resource" value={v.resource} onChange={(x) => f.set({ resource: x })} options={[{ value: "", label: "All resources" }, ...items.map((r) => ({ value: r.name, label: r.title || r.name }))]} />
        {f.dirty && (
          <button type="button" onClick={f.reset} className="text-xs font-medium text-accent hover:underline">
            Reset
          </button>
        )}
      </div>

      <Q q={list} rows={4}>
        {(d) => (
          <>
            <GrantsTable
              grants={shown}
              empty={v.live === "1" ? "Nobody holds live access" : "No grants match"}
              onRevoke={async (g, reason) => {
                await admin.revokeGrant(g.principal!, g.id, reason);
                await qc.invalidateQueries({ queryKey: ["admin"] });
              }}
            />
            <Pager
              page={f.page}
              size={PAGE}
              shown={shown.length}
              total={total}
              hasMore={legacyPaging ? (f.page + 1) * PAGE < rows.length : d.hasMore}
              onPage={f.setPage}
              fetching={list.isPlaceholderData}
              className="border-t border-line"
            />
          </>
        )}
      </Q>
    </div>
  );
}
