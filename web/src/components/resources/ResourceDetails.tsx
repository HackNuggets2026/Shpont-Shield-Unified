import type { CatalogItem, Workflow } from "../../api";
import { countC, usdC } from "../../lib/compact";
import { Dialog } from "../Dialog";
import { SensitivityPill } from "../pills";
import { Button, Stat } from "../ui";
import { IconKey } from "../icons";
import { CopyText } from "./CopyText";
import { CLASS_INFO, METER_LABEL, grantLength, plainPrice, usageShort, workflowsFor } from "./catalogInfo";

/** Everything technical about one resource (identifier, meter, limits), kept out of the main tables. */
export function ResourceDetails({
  r,
  menu,
  onClose,
  onGrant,
}: {
  r: CatalogItem | null;
  menu?: Workflow[];
  onClose: () => void;
  onGrant?: (resource: string) => void;
}) {
  if (!r) return null;
  const wfs = workflowsFor(r, menu);
  return (
    <Dialog
      open
      wide
      onClose={onClose}
      title={r.title || r.name}
      footer={
        r.class === "access_grant" && onGrant ? (
          <Button
            variant="primary"
            onClick={() => {
              onClose();
              onGrant(r.name);
            }}
          >
            <IconKey size={14} /> Grant access
          </Button>
        ) : undefined
      }
    >
      <div className="space-y-4 text-sm">
        {r.description && <p className="text-ink2">{r.description}</p>}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Stat label="Price" value={<span title={plainPrice(r)}>{plainPrice(r)}</span>} />
          <Stat label="Owner" value={r.owner || "unassigned"} />
          <Stat label="Sensitivity" value={<SensitivityPill s={r.sensitivity} />} />
          {r.class !== "access_grant" && <Stat label="Spend, 30 days" value={usdC(r.usage.usd)} />}
          <Stat label="Usage, 30 days" value={usageShort(r)} />
          {r.class === "leasable" && <Stat label="Running now" value={countC(r.live_leases)} />}
          {r.class === "access_grant" && <Stat label="Live grants" value={countC(r.live_grants)} />}
        </div>
        {r.lease && (
          <div className="rounded-lg bg-raised px-3 py-2 text-xs text-ink2">
            {r.lease.max_concurrent_per_principal ? `At most ${r.lease.max_concurrent_per_principal} per person at a time. ` : "No per-person cap. "}
            {r.lease.idle_minutes ? `Counts as a zombie after ${r.lease.idle_minutes} min idle` : "No idle limit"}
            {r.lease.idle_minutes ? (r.lease.auto_reclaim ? ", then it is stopped automatically." : "; it is flagged but not stopped automatically.") : "."}
          </div>
        )}
        {r.grant && (
          <div className="rounded-lg bg-raised px-3 py-2 text-xs text-ink2">
            {r.grant.approval === "workflow" ? "An admin grant, or a workflow that includes it, " : "Only an admin grant "}
            unlocks it, for up to {grantLength(r.grant.max_minutes)} at a time.
            {r.actions.length > 0 && <> Actions: <span className="font-mono">{r.actions.join(", ")}</span>.</>}
          </div>
        )}
        <div>
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">Workflows that use it</div>
          {wfs.length ? (
            <div className="flex flex-wrap gap-1">
              {wfs.map((w) => (
                <span key={w.name} className="rounded bg-ink/[0.06] px-1.5 py-px font-mono text-[11px] text-ink2" title={w.via === "resource" ? "lists this resource" : `allows this ${w.via}`}>
                  {w.name}
                </span>
              ))}
            </div>
          ) : (
            <span className="text-xs text-muted">none</span>
          )}
        </div>
        <details className="rounded-lg border border-line px-3 py-2 text-xs">
          <summary className="cursor-pointer font-medium text-ink2">Technical details</summary>
          <div className="mt-2 space-y-1.5 text-ink2">
            <div>
              Identifier: <CopyText text={r.urn} />
            </div>
            <div>
              Catalog name: <span className="font-mono">{r.name}</span> · class <span className="font-mono">{r.class}</span> ({CLASS_INFO[r.class].short.toLowerCase()})
            </div>
            <div>
              Measured by: {METER_LABEL[r.meter] ?? r.meter.replace(/_/g, " ")} · provider {r.provider || "—"} · category {r.category || "—"}
            </div>
            {(r.models.length > 0 || r.tools.length > 0) && (
              <div className="font-mono text-[11px]">{[...r.models, ...r.tools].join(", ")}</div>
            )}
          </div>
        </details>
      </div>
    </Dialog>
  );
}
