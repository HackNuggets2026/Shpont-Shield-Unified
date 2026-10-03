import { useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { admin, downloadExport, type Catalog, type CatalogItem, type Timeseries } from "../../api";
import { GrantDialog, GrantsTable } from "../../components/Grants";
import { LeasesTable } from "../../components/Leases";
import { StackedChart } from "../../components/charts";
import { ClassPill } from "../../components/pills";
import { Button, Card, ErrorBox, PageHeader, Pill, Q, Segmented, cx } from "../../components/ui";
import { IconDownload, IconKey } from "../../components/icons";
import { CatalogTable } from "../../components/resources/CatalogTable";
import { LiveLeases, RecentReclaims } from "../../components/resources/LiveLeases";
import { CLASSES, CLASS_INFO, type ResourceClass } from "../../components/resources/catalogInfo";
import { usd } from "../../lib/format";

export { priceLabel } from "../../components/resources/catalogInfo";

/** The timeseries with catalog titles as series names, so the legend reads "Sandbox VM", not "vm". */
function titled(ts: Timeseries, byName: Record<string, CatalogItem>): Timeseries {
  const series: Record<string, number[]> = {};
  // Recharts reads a dataKey with "." as a path, so a title like "Llama 3.2" would plot nothing: keep the name then.
  for (const [k, v] of Object.entries(ts.series)) {
    const t = byName[k]?.title;
    series[t && !t.includes(".") && !(t in series) ? t : k] = v;
  }
  return { ...ts, series };
}

function ClassTiles({ catalog, active, onPick }: { catalog: Catalog | undefined; active: ResourceClass; onPick: (c: ResourceClass) => void }) {
  return (
    <div className="grid gap-3 md:grid-cols-3">
      {CLASSES.map((c) => {
        const items = catalog?.classes[c] ?? [];
        const spend = items.reduce((s, r) => s + r.usage.usd, 0);
        const live =
          c === "leasable"
            ? `${items.reduce((s, r) => s + r.live_leases, 0)} running`
            : c === "access_grant"
              ? `${items.reduce((s, r) => s + r.live_grants, 0)} live`
              : `${items.filter((r) => r.usage.usd > 0).length} with spend`;
        return (
          <button
            key={c}
            type="button"
            onClick={() => onPick(c)}
            aria-pressed={active === c}
            className={cx(
              "rounded-xl border bg-panel p-4 text-left shadow-sm transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
              active === c ? "border-accent/60" : "border-line hover:border-accent/30",
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <ClassPill cls={c} />
              <span className="text-xs text-muted">{catalog ? `${items.length} resources` : "…"}</span>
            </div>
            <div className="mt-2 text-sm font-medium text-ink">{CLASS_INFO[c].short}</div>
            <p className="mt-1 hidden text-xs leading-relaxed text-muted sm:block">{CLASS_INFO[c].long}</p>
            <div className="mt-3 flex items-baseline justify-between gap-2 border-t border-line pt-2 text-xs">
              <span className="text-muted">{c === "access_grant" ? "no metered cost" : `${usd(spend)} in 30 days`}</span>
              <span className="font-medium text-ink2">{catalog ? live : ""}</span>
            </div>
          </button>
        );
      })}
    </div>
  );
}

export function Resources() {
  const qc = useQueryClient();
  const catalog = useQuery({ queryKey: ["admin", "catalog"], queryFn: () => admin.catalog(), refetchInterval: 30_000 });
  const leases = useQuery({ queryKey: ["admin", "leases"], queryFn: admin.leases, refetchInterval: 5_000 });
  const grants = useQuery({ queryKey: ["admin", "grants"], queryFn: () => admin.grants(), refetchInterval: 10_000 });
  const menu = useQuery({ queryKey: ["admin", "menu"], queryFn: admin.menu, refetchInterval: 60_000 });
  const spend = useQuery({
    queryKey: ["admin", "timeseries", "resource", 30],
    queryFn: () => admin.timeseries("resource", 30),
    refetchInterval: 30_000,
  });
  const [cls, setCls] = useState<ResourceClass>("consumable");
  const [granting, setGranting] = useState<{ resource?: string } | null>(null);
  const [showExpired, setShowExpired] = useState(false);
  const [exporting, setExporting] = useState<string | null>(null);
  const [exportMsg, setExportMsg] = useState<string | null>(null);
  const [exportErr, setExportErr] = useState<unknown>(null);
  const catalogRef = useRef<HTMLDivElement>(null);

  const byName = useMemo(() => {
    const m: Record<string, CatalogItem> = {};
    if (catalog.data) for (const c of CLASSES) for (const r of catalog.data.classes[c]) m[r.name] = r;
    return m;
  }, [catalog.data]);

  const exp = async (path: string, name: string) => {
    setExportErr(null);
    setExportMsg(null);
    setExporting(name);
    try {
      await downloadExport(path, name);
      setExportMsg(`Downloaded ${name}`);
    } catch (e) {
      setExportErr(e);
    } finally {
      setExporting(null);
    }
  };

  const invalidate = () => qc.invalidateQueries({ queryKey: ["admin"] });
  const zombies = leases.data?.open.filter((l) => l.flags.length).length ?? 0;
  const liveGrants = grants.data?.filter((g) => g.live).length;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Resources"
        subtitle="Everything an agent can touch, catalogued, metered and limited: what it costs, what's running, and who holds access."
        actions={
          <>
            <Button onClick={() => setGranting({})} variant="primary">
              <IconKey size={14} /> Grant access
            </Button>
            <Button
              onClick={() => exp("/api/admin/export/focus?days=30", "focus-ai-spend-30d.csv")}
              disabled={exporting !== null}
              title="Last 30 days of AI spend as a FinOps FOCUS 1.1 CSV"
            >
              <IconDownload size={14} /> {exporting?.startsWith("focus") ? "Exporting…" : "FOCUS CSV"}
            </Button>
            <Button
              variant="ghost"
              onClick={() => exp("/api/admin/export/backstage", "catalog-backstage.yaml")}
              disabled={exporting !== null}
              title="The catalog as Backstage Resource entities"
            >
              <IconDownload size={14} /> {exporting?.startsWith("catalog") ? "Exporting…" : "Backstage YAML"}
            </Button>
          </>
        }
      />
      {exportErr != null && <ErrorBox error={exportErr} compact />}
      {exportMsg && (
        <div className="rounded-lg bg-good/10 px-3 py-2 text-xs text-good ring-1 ring-inset ring-good/25" role="status">
          {exportMsg}
        </div>
      )}

      <ClassTiles
        catalog={catalog.data}
        active={cls}
        onPick={(c) => {
          setCls(c);
          catalogRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        }}
      />

      <Card
        title="Running now"
        subtitle="Open leases on simulators and VMs. Refreshes every 5 s; idle time counts toward the zombie limit."
        flush
        actions={
          leases.data ? (
            <div className="flex items-center gap-2">
              {zombies > 0 && (
                <Pill tone="warn" dot>
                  {zombies} zombie
                </Pill>
              )}
              <Pill tone="accent">{leases.data.open.length} open</Pill>
            </div>
          ) : undefined
        }
      >
        <Q q={leases} rows={3}>
          {(d) => (
            <>
              <LiveLeases
                leases={d.open}
                fetchedAt={leases.dataUpdatedAt}
                catalog={byName}
                onStop={async (id) => {
                  const r = await admin.releaseLease(id);
                  await invalidate();
                  return r;
                }}
              />
              <RecentReclaims recent={d.recent} catalog={byName} />
            </>
          )}
        </Q>
      </Card>

      <Card
        title="Access grants"
        subtitle="Time-boxed access to sensitive systems (resource, actions, optional workflow scope, expiry)"
        flush
        actions={
          <>
            {liveGrants !== undefined && <Pill tone="cc">{liveGrants} live</Pill>}
            <Segmented
              value={showExpired ? "all" : "live"}
              onChange={(v) => setShowExpired(v === "all")}
              options={[
                { value: "live", label: "Live" },
                { value: "all", label: "Include expired" },
              ]}
            />
            <Button size="sm" onClick={() => setGranting({})}>
              <IconKey size={13} /> Grant access
            </Button>
          </>
        }
      >
        <Q q={grants} rows={3}>
          {(d) => (
            <GrantsTable
              grants={showExpired ? d : d.filter((g) => g.live)}
              empty={showExpired ? "No grants yet" : "No live grants. Approve a request or grant access to see one here."}
              onRevoke={async (g, reason) => {
                await admin.revokeGrant(g.principal!, g.id, reason);
                await invalidate();
              }}
            />
          )}
        </Q>
      </Card>

      <div ref={catalogRef} className="scroll-mt-4">
        <Card
          title="Catalog"
          subtitle={CLASS_INFO[cls].long}
          flush
          actions={
            <Segmented<ResourceClass>
              value={cls}
              onChange={setCls}
              options={CLASSES.map((c) => ({
                value: c,
                label: `${CLASS_INFO[c].title}${catalog.data ? ` (${catalog.data.classes[c].length})` : ""}`,
              }))}
            />
          }
        >
          <Q q={catalog} rows={6}>
            {(d) => (
              <>
                <CatalogTable
                  cls={cls}
                  items={[...d.classes[cls]].sort((a, b) => b.usage.usd - a.usage.usd)}
                  menu={menu.data?.workflows}
                  onGrant={(resource) => setGranting({ resource })}
                />
                {d.uncatalogued.length > 0 && (
                  <div className="border-t border-line bg-warn/[0.06] px-4 py-3 text-xs">
                    <div className="mb-1 font-medium text-warn">Spend on resources missing from the catalog</div>
                    <div className="flex flex-wrap gap-2">
                      {d.uncatalogued.map((u) => (
                        <span key={u.resource} className="font-mono text-ink2">
                          {u.resource} {usd(u.usd)}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </Q>
        </Card>
      </div>

      <Card title="Spend by resource" subtitle="Daily cost per catalog entry, last 30 days. Click a legend entry to hide it.">
        <Q q={spend} rows={6}>
          {(ts) => <StackedChart ts={titled(ts, byName)} kind="bar" metric="usd" />}
        </Q>
      </Card>

      <Card title="Recently stopped" subtitle="Leases that ended: stopped by the agent, by an admin, or reclaimed as zombies" flush>
        <Q q={leases} rows={3}>
          {(d) => <LeasesTable leases={d.recent.slice(0, 10)} closed empty="No leases have ended yet" />}
        </Q>
      </Card>

      <GrantDialog open={granting !== null} resource={granting?.resource} onClose={() => setGranting(null)} />
    </div>
  );
}
