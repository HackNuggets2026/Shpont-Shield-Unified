import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { admin, downloadExport, type CatalogItem } from "../../api";
import { GrantDialog, GrantsTable } from "../../components/Grants";
import { LeasesTable } from "../../components/Leases";
import { CLASS_LABEL, ClassPill, SensitivityPill } from "../../components/pills";
import { Button, Card, Chips, Empty, ErrorBox, PageHeader, Pill, Q, Segmented, TableWrap } from "../../components/ui";
import { IconDownload, IconKey } from "../../components/icons";
import { minutes, num, tokens, usd } from "../../lib/format";

export function priceLabel(r: CatalogItem): string {
  const p = r.price;
  const parts: string[] = [];
  if (p.usd_per_1m_input || p.usd_per_1m_output) parts.push(`${usd(p.usd_per_1m_input)} in / ${usd(p.usd_per_1m_output)} out per 1M`);
  if (p.usd_per_compute_second) parts.push(`${usd(p.usd_per_compute_second)}/GPU-s`);
  if (p.usd_per_unit) {
    if (r.unit === "usd") parts.push("billed at cost");
    else parts.push(`${usd(p.usd_per_unit)}/${r.unit === "minute" ? "min" : r.unit}`);
  }
  if (!parts.length) return r.class === "access_grant" ? "no charge" : r.meter === "telemetry" ? "as reported" : "free";
  return parts.join(" · ");
}

const CLASS_HINT: Record<string, string> = {
  consumable: "Metered per token, minute or dollar: models, CI minutes, cloud bills.",
  leasable: "Started and stopped by agents (simulators, VMs). Idle leases are flagged as zombies and reclaimed.",
  access_grant: "Sensitive systems an agent may only touch with a time-boxed grant.",
};

function CatalogTable({ items }: { items: CatalogItem[] }) {
  if (!items.length) return <Empty title="Nothing in this class" />;
  return (
    <TableWrap>
      <table className="tbl min-w-[980px]">
        <thead>
          <tr>
            <th>Resource</th>
            <th>Price</th>
            <th>Meter</th>
            <th>Sensitivity</th>
            <th>Owner</th>
            <th className="text-right">30d cost</th>
            <th className="text-right">30d volume</th>
            <th className="text-right">Live</th>
            <th>Workflows</th>
          </tr>
        </thead>
        <tbody>
          {items.map((r) => (
            <tr key={r.name}>
              <td className="max-w-[300px]">
                <div className="font-medium text-ink">{r.title || r.name}</div>
                <div className="truncate font-mono text-[11px] text-muted" title={r.urn}>
                  {r.urn}
                </div>
              </td>
              <td className="text-xs text-ink2">{priceLabel(r)}</td>
              <td>
                <Pill tone="neutral">{r.meter.replace(/_/g, " ")}</Pill>
              </td>
              <td>
                <SensitivityPill s={r.sensitivity} />
              </td>
              <td className="text-xs text-ink2">{r.owner || <span className="text-muted">—</span>}</td>
              <td className="tnum text-right font-medium">{usd(r.usage.usd)}</td>
              <td className="tnum text-right text-xs text-ink2">
                {r.usage.tokens ? `${tokens(r.usage.tokens)} tok` : r.usage.minutes ? minutes(r.usage.minutes) : r.usage.requests ? `${num(r.usage.requests)} calls` : "—"}
              </td>
              <td className="text-right">
                {r.class === "leasable" ? (
                  r.live_leases ? <Pill tone="accent">{r.live_leases} running</Pill> : <span className="text-muted">0</span>
                ) : r.class === "access_grant" ? (
                  r.live_grants ? <Pill tone="cc">{r.live_grants} granted</Pill> : <span className="text-muted">0</span>
                ) : (
                  <span className="text-muted">—</span>
                )}
              </td>
              <td>
                <Chips items={r.workflows} max={2} empty="—" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

type Cls = "consumable" | "leasable" | "access_grant";

export function Resources() {
  const qc = useQueryClient();
  const catalog = useQuery({ queryKey: ["admin", "catalog"], queryFn: admin.catalog, refetchInterval: 30_000 });
  const leases = useQuery({ queryKey: ["admin", "leases"], queryFn: admin.leases, refetchInterval: 5_000 });
  const grants = useQuery({ queryKey: ["admin", "grants"], queryFn: admin.grants, refetchInterval: 15_000 });
  const [cls, setCls] = useState<Cls>("consumable");
  const [granting, setGranting] = useState(false);
  const [showExpired, setShowExpired] = useState(false);
  const [exportErr, setExportErr] = useState<unknown>(null);

  const exp = async (path: string, name: string) => {
    setExportErr(null);
    try {
      await downloadExport(path, name);
    } catch (e) {
      setExportErr(e);
    }
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Resources"
        subtitle="The catalog of everything AI work consumes, what's running right now, and who holds access."
        actions={
          <>
            <Button onClick={() => setGranting(true)} variant="primary">
              <IconKey size={14} /> Grant access
            </Button>
            <Button onClick={() => exp("/api/admin/export/focus?days=30", "focus-ai-spend.csv")} title="FinOps FOCUS 1.1 cost export">
              <IconDownload size={14} /> FOCUS CSV
            </Button>
            <Button variant="ghost" onClick={() => exp("/api/admin/export/backstage", "catalog-backstage.yaml")}>
              <IconDownload size={14} /> Backstage
            </Button>
          </>
        }
      />
      {exportErr != null && <ErrorBox error={exportErr} compact />}

      <Card
        title="Running now"
        subtitle="Open leases on simulators, VMs and other leasable resources"
        flush
        actions={leases.data && leases.data.open.some((l) => l.flags.length) ? <Pill tone="warn" dot>{leases.data.open.filter((l) => l.flags.length).length} zombie</Pill> : undefined}
      >
        <Q q={leases} rows={3}>
          {(d) => (
            <LeasesTable
              leases={d.open}
              empty="Nothing running"
              onStop={async (id) => {
                const r = await admin.releaseLease(id);
                await qc.invalidateQueries({ queryKey: ["admin"] });
                return r;
              }}
            />
          )}
        </Q>
      </Card>

      <Card
        title="Access grants"
        subtitle="Time-boxed access to sensitive systems"
        flush
        actions={
          <Segmented
            value={showExpired ? "all" : "live"}
            onChange={(v) => setShowExpired(v === "all")}
            options={[
              { value: "live", label: "Live" },
              { value: "all", label: "Include expired" },
            ]}
          />
        }
      >
        <Q q={grants} rows={3}>
          {(d) => (
            <GrantsTable
              grants={showExpired ? d : d.filter((g) => g.live)}
              empty={showExpired ? "No grants yet" : "No live grants"}
              onRevoke={async (g, reason) => {
                await admin.revokeGrant(g.principal!, g.id, reason);
                await qc.invalidateQueries({ queryKey: ["admin"] });
              }}
            />
          )}
        </Q>
      </Card>

      <Card
        title="Catalog"
        subtitle={CLASS_HINT[cls]}
        flush
        actions={
          <Segmented<Cls>
            value={cls}
            onChange={setCls}
            options={(["consumable", "leasable", "access_grant"] as Cls[]).map((c) => ({
              value: c,
              label: `${CLASS_LABEL[c]}${catalog.data ? ` (${catalog.data.classes[c].length})` : ""}`,
            }))}
          />
        }
      >
        <Q q={catalog} rows={6}>
          {(d) => (
            <>
              <CatalogTable items={d.classes[cls]} />
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

      <Card title="Recently stopped" flush>
        <Q q={leases} rows={3}>
          {(d) => <LeasesTable leases={d.recent.slice(0, 15)} closed empty="No leases have ended yet" />}
        </Q>
      </Card>

      <p className="text-xs text-muted">
        Classes: <ClassPill cls="consumable" /> <ClassPill cls="leasable" /> <ClassPill cls="access_grant" />
      </p>

      <GrantDialog open={granting} onClose={() => setGranting(false)} />
    </div>
  );
}
