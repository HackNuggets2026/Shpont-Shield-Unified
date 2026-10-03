import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { admin, downloadExport, type CatalogItem, type Timeseries } from "../../api";
import { ops } from "../../opsApi";
import { GrantDialog } from "../../components/Grants";
import { StackedChart } from "../../components/charts";
import { Button, Card, ErrorBox, PageHeader, Q, cx } from "../../components/ui";
import { IconBox, IconDownload, IconKey, IconLock, IconReceipt } from "../../components/icons";
import { ResourceDetails } from "../../components/resources/ResourceDetails";
import { RunningNow } from "../../components/resources/RunningNow";
import { SensitiveAccess } from "../../components/resources/SensitiveAccess";
import { SpendByDepartment } from "../../components/resources/SpendByDepartment";
import { SpendTable } from "../../components/resources/SpendTable";
import { CLASSES } from "../../components/resources/catalogInfo";
import { countC, exact, usdC } from "../../lib/compact";

export { priceLabel } from "../../components/resources/catalogInfo";

type Tab = "spend" | "running" | "access";
const TABS: { id: Tab; title: string; line: string; icon: ReactNode }[] = [
  { id: "spend", title: "Spend", line: "What costs money: AI models, Claude Code, CI minutes and the cloud bill.", icon: <IconReceipt size={16} /> },
  { id: "running", title: "Running now", line: "Simulators and VMs agents start for testing. Idle ones are stopped automatically.", icon: <IconBox size={16} /> },
  { id: "access", title: "Sensitive access", line: "Production data, deploys, external email. Usable only with a time-limited grant.", icon: <IconLock size={16} /> },
];

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

/** A small "Export" menu: the FinOps CSV and the Backstage catalog. */
function ExportMenu({ onPick, busy }: { onPick: (path: string, file: string) => void; busy: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  const item = (path: string, file: string, title: string, sub: string) => (
    <button
      type="button"
      role="menuitem"
      className="block w-full px-3 py-2 text-left hover:bg-raised"
      onClick={() => {
        setOpen(false);
        onPick(path, file);
      }}
    >
      <div className="text-sm font-medium text-ink">{title}</div>
      <div className="text-[11px] text-muted">{sub}</div>
    </button>
  );
  return (
    <div ref={ref} className="relative">
      <Button onClick={() => setOpen((o) => !o)} disabled={busy} aria-haspopup="menu" aria-expanded={open}>
        <IconDownload size={14} /> {busy ? "Exporting…" : "Export"}
      </Button>
      {open && (
        <div role="menu" className="absolute right-0 z-20 mt-1 w-64 overflow-hidden rounded-lg border border-line bg-panel py-1 shadow-xl">
          {item("/api/admin/export/focus?days=30", "focus-ai-spend-30d.csv", "Spend, last 30 days", "CSV for FinOps tools (FOCUS 1.1)")}
          {item("/api/admin/export/backstage", "catalog-backstage.yaml", "Resource list", "YAML for a Backstage service catalog")}
        </div>
      )}
    </div>
  );
}

function TabButton({ t, active, onPick, figure, sub }: { t: (typeof TABS)[number]; active: boolean; onPick: () => void; figure: ReactNode; sub: ReactNode }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onPick}
      className={cx(
        "min-w-0 rounded-xl border bg-panel p-4 text-left shadow-sm transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
        active ? "border-accent ring-1 ring-accent/40" : "border-line hover:border-accent/40",
      )}
    >
      <div className={cx("flex items-center gap-2 text-sm font-semibold", active ? "text-accent" : "text-ink")}>
        {t.icon}
        {t.title}
      </div>
      <div className="tnum mt-2 text-2xl font-semibold tracking-tight text-ink">{figure}</div>
      <div className="mt-0.5 truncate text-xs text-muted">{sub}</div>
    </button>
  );
}

export function Resources() {
  const [sp, setSp] = useSearchParams();
  const tab = (TABS.some((t) => t.id === sp.get("tab")) ? sp.get("tab") : "spend") as Tab;
  const setTab = (t: Tab) =>
    setSp(
      (prev) => {
        // Each section keeps its own filters (l_*, g_*); switching only changes the tab.
        const n = new URLSearchParams(prev);
        if (t === "spend") n.delete("tab");
        else n.set("tab", t);
        return n;
      },
      { replace: true },
    );

  const catalog = useQuery({ queryKey: ["admin", "catalog"], queryFn: () => admin.catalog(), refetchInterval: 30_000 });
  const overview = useQuery({ queryKey: ["admin", "overview"], queryFn: admin.overview, refetchInterval: 5_000 });
  const grantSum = useQuery({ queryKey: ["admin", "grants", "summary"], queryFn: () => ops.grants({ live: true, limit: 1 }), refetchInterval: 10_000 });
  const menu = useQuery({ queryKey: ["admin", "menu"], queryFn: admin.menu, refetchInterval: 60_000 });
  const spend = useQuery({
    queryKey: ["admin", "timeseries", "resource", 30],
    queryFn: () => admin.timeseries("resource", 30),
    enabled: tab === "spend",
    refetchInterval: 30_000,
  });
  const [granting, setGranting] = useState<{ resource?: string } | null>(null);
  const [detail, setDetail] = useState<CatalogItem | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportMsg, setExportMsg] = useState<string | null>(null);
  const [exportErr, setExportErr] = useState<unknown>(null);

  const byName = useMemo(() => {
    const m: Record<string, CatalogItem> = {};
    if (catalog.data) for (const c of CLASSES) for (const r of catalog.data.classes[c]) m[r.name] = r;
    return m;
  }, [catalog.data]);
  const c = catalog.data?.classes;
  // Spend covers everything with a price: consumables, plus what leasables cost while held.
  const costly = useMemo(() => (c ? [...c.consumable, ...c.leasable].sort((a, b) => b.usage.usd - a.usage.usd) : []), [c]);
  const spend30 = costly.reduce((s, r) => s + r.usage.usd, 0);

  const exp = async (path: string, name: string) => {
    setExportErr(null);
    setExportMsg(null);
    setExporting(true);
    try {
      await downloadExport(path, name);
      setExportMsg(`Downloaded ${name}`);
    } catch (e) {
      setExportErr(e);
    } finally {
      setExporting(false);
    }
  };

  const o = overview.data;
  const gs = grantSum.data;
  const liveGrants = gs ? (gs.summary?.live ?? gs.rows.filter((g) => g.live).length) : undefined;
  const soonGrants = gs?.summary?.expiring_1h;
  const current = TABS.find((t) => t.id === tab)!;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Resources"
        subtitle="What agents use: what it costs, what is running right now, and who can touch sensitive systems."
        actions={
          <>
            <Button onClick={() => setGranting({})} variant="primary">
              <IconKey size={14} /> Grant access
            </Button>
            <ExportMenu onPick={exp} busy={exporting} />
          </>
        }
      />
      {exportErr != null && <ErrorBox error={exportErr} compact />}
      {exportMsg && (
        <div className="rounded-lg bg-good/10 px-3 py-2 text-xs text-good ring-1 ring-inset ring-good/25" role="status">
          {exportMsg}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-3" role="tablist">
        <TabButton
          t={TABS[0]}
          active={tab === "spend"}
          onPick={() => setTab("spend")}
          figure={catalog.data ? <span title={exact(spend30, true)}>{usdC(spend30)}</span> : "…"}
          sub="spent in the last 30 days"
        />
        <TabButton
          t={TABS[1]}
          active={tab === "running"}
          onPick={() => setTab("running")}
          figure={o ? countC(o.leases_open) : "…"}
          sub={o ? `${o.zombies ? `${countC(o.zombies)} idle too long · ` : ""}${usdC(o.leases_running_usd)} so far` : "held right now"}
        />
        <TabButton
          t={TABS[2]}
          active={tab === "access"}
          onPick={() => setTab("access")}
          figure={liveGrants === undefined ? "…" : countC(liveGrants)}
          sub={soonGrants ? `live grants · ${countC(soonGrants)} expire within the hour` : "live grants right now"}
        />
      </div>

      <Card title={current.title} subtitle={current.line} flush>
        {catalog.isError ? (
          <div className="p-4">
            <ErrorBox error={catalog.error} retry={() => catalog.refetch()} />
          </div>
        ) : tab === "spend" ? (
          <Q q={catalog} rows={6}>
            {(d) => (
              <>
                <SpendTable items={costly} onOpen={setDetail} />
                {d.uncatalogued.length > 0 && (
                  <div className="border-t border-line bg-warn/[0.06] px-4 py-3 text-xs">
                    <div className="mb-1 font-medium text-warn">Spend on things nobody has described yet</div>
                    <div className="flex flex-wrap gap-2">
                      {d.uncatalogued.map((u) => (
                        <span key={u.resource} className="text-ink2">
                          <span className="font-mono">{u.resource}</span> {usdC(u.usd)}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </Q>
        ) : tab === "running" ? (
          <RunningNow catalog={byName} leasable={c?.leasable ?? []} />
        ) : (
          <SensitiveAccess items={c?.access_grant ?? []} onGrant={(resource) => setGranting({ resource })} onOpen={setDetail} />
        )}
      </Card>

      {tab === "spend" && (
        <div className="grid gap-4 xl:grid-cols-5">
          <Card className="xl:col-span-3" title="Daily spend" subtitle="Per resource, last 30 days. Click a legend entry to hide it.">
            <Q q={spend} rows={6}>
              {(ts) => <StackedChart ts={titled(ts, byName)} kind="bar" metric="usd" />}
            </Q>
          </Card>
          <Card className="xl:col-span-2" title="By department" subtitle="Last 30 days, with each department's biggest costs">
            <SpendByDepartment catalog={byName} />
          </Card>
        </div>
      )}

      <ResourceDetails r={detail} menu={menu.data?.workflows} onClose={() => setDetail(null)} onGrant={(resource) => setGranting({ resource })} />
      <GrantDialog open={granting !== null} resource={granting?.resource} onClose={() => setGranting(null)} />
    </div>
  );
}
