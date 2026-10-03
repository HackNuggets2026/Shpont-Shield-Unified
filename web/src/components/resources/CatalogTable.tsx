import type { ReactNode } from "react";
import type { CatalogItem, Workflow } from "../../api";
import { usd } from "../../lib/format";
import { SensitivityPill } from "../pills";
import { Button, Empty, Pill, TableWrap } from "../ui";
import { IconKey } from "../icons";
import { CopyText } from "./CopyText";
import { METER_LABEL, grantLength, priceLabel, usageLabel, workflowsFor, type ResourceClass } from "./catalogInfo";

function WorkflowChips({ r, menu }: { r: CatalogItem; menu?: Workflow[] }) {
  const wfs = workflowsFor(r, menu);
  if (!wfs.length) return <span className="text-xs text-muted">none</span>;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {wfs.map((w) => (
        <span
          key={w.name}
          title={w.via === "resource" ? `${w.name} lists this resource` : `${w.name} allows this ${w.via}`}
          className="whitespace-nowrap rounded bg-ink/[0.06] px-1.5 py-px font-mono text-[11px] text-ink2"
        >
          {w.name}
          {w.via !== "resource" && <span className="text-muted"> ·{w.via}</span>}
        </span>
      ))}
    </span>
  );
}

function ResourceCell({ r }: { r: CatalogItem }) {
  return (
    <td className="max-w-[300px]">
      <div className="font-medium text-ink">{r.title || r.name}</div>
      <CopyText text={r.urn} />
      {r.description && <div className="mt-0.5 line-clamp-2 text-[11px] text-ink2">{r.description}</div>}
    </td>
  );
}

const Owner = ({ r }: { r: CatalogItem }) => (
  <td className="text-xs text-ink2">{r.owner || <span className="text-muted">unassigned</span>}</td>
);

const Meter = ({ r }: { r: CatalogItem }) => (
  <td>
    <Pill tone="neutral">{METER_LABEL[r.meter] ?? r.meter.replace(/_/g, " ")}</Pill>
  </td>
);

/** One catalog class as a table; columns fit the class (budgets vs. leases vs. grants). */
export function CatalogTable({
  cls,
  items,
  menu,
  onGrant,
}: {
  cls: ResourceClass;
  items: CatalogItem[];
  menu?: Workflow[];
  onGrant?: (resource: string) => void;
}) {
  if (!items.length) return <Empty title="Nothing in this class" hint="Add entries under catalog: in policy.yaml." />;
  const total = items.reduce((s, r) => s + r.usage.usd, 0);
  const spendCell = (r: CatalogItem) => (
    <td className="tnum text-right">
      <div className="font-medium text-ink">{usd(r.usage.usd)}</div>
      {total > 0 && r.usage.usd > 0 && <div className="text-[11px] text-muted">{Math.round((r.usage.usd / total) * 100)}% of class</div>}
    </td>
  );

  let head: ReactNode;
  let row: (r: CatalogItem) => ReactNode;
  if (cls === "consumable") {
    head = (
      <tr>
        <th>Resource</th>
        <th>Unit price</th>
        <th>Meter</th>
        <th>Sensitivity</th>
        <th>Owner</th>
        <th className="text-right">30d spend</th>
        <th>30d usage</th>
        <th>Workflows</th>
      </tr>
    );
    row = (r) => (
      <>
        <ResourceCell r={r} />
        <td className="max-w-[220px] text-xs text-ink2">{priceLabel(r)}</td>
        <Meter r={r} />
        <td>
          <SensitivityPill s={r.sensitivity} />
        </td>
        <Owner r={r} />
        {spendCell(r)}
        <td className="text-xs text-ink2">{usageLabel(r)}</td>
        <td>
          <WorkflowChips r={r} menu={menu} />
        </td>
      </>
    );
  } else if (cls === "leasable") {
    head = (
      <tr>
        <th>Resource</th>
        <th>Unit price</th>
        <th>Limits</th>
        <th>Meter</th>
        <th>Sensitivity</th>
        <th>Owner</th>
        <th className="text-right">30d spend</th>
        <th>30d usage</th>
        <th>Now</th>
        <th>Workflows</th>
      </tr>
    );
    row = (r) => (
      <>
        <ResourceCell r={r} />
        <td className="whitespace-nowrap text-xs text-ink2">{priceLabel(r)}</td>
        <td className="text-[11px] leading-relaxed text-ink2">
          <div>{r.lease?.max_concurrent_per_principal ? `max ${r.lease.max_concurrent_per_principal} per person` : "no concurrency cap"}</div>
          <div>{r.lease?.idle_minutes ? `zombie after ${r.lease.idle_minutes} min idle` : "no idle limit"}</div>
          <div className={r.lease?.auto_reclaim ? "text-good" : "text-muted"}>{r.lease?.auto_reclaim ? "auto-reclaim on" : "auto-reclaim off"}</div>
        </td>
        <Meter r={r} />
        <td>
          <SensitivityPill s={r.sensitivity} />
        </td>
        <Owner r={r} />
        {spendCell(r)}
        <td className="text-xs text-ink2">{usageLabel(r)}</td>
        <td>{r.live_leases ? <Pill tone="accent" dot>{r.live_leases} running</Pill> : <span className="text-xs text-muted">idle</span>}</td>
        <td>
          <WorkflowChips r={r} menu={menu} />
        </td>
      </>
    );
  } else {
    head = (
      <tr>
        <th>Resource</th>
        <th>Actions</th>
        <th>Approval</th>
        <th>Meter</th>
        <th>Sensitivity</th>
        <th>Owner</th>
        <th>Live grants</th>
        <th>Workflows</th>
        {onGrant && <th />}
      </tr>
    );
    row = (r) => (
      <>
        <ResourceCell r={r} />
        <td>
          <span className="inline-flex flex-wrap gap-1">
            {r.actions.map((a) => (
              <span key={a} className="rounded bg-ink/[0.06] px-1.5 py-px font-mono text-[11px] text-ink2">
                {a}
              </span>
            ))}
          </span>
        </td>
        <td className="text-[11px] leading-relaxed text-ink2">
          <div>{r.grant?.approval === "workflow" ? "admin, or a workflow that includes it" : "admin approval"}</div>
          <div className="text-muted">up to {grantLength(r.grant?.max_minutes)} per grant</div>
        </td>
        <Meter r={r} />
        <td>
          <SensitivityPill s={r.sensitivity} />
        </td>
        <Owner r={r} />
        <td>{r.live_grants ? <Pill tone="cc" dot>{r.live_grants} live</Pill> : <span className="text-xs text-muted">none</span>}</td>
        <td>
          <WorkflowChips r={r} menu={menu} />
        </td>
        {onGrant && (
          <td className="text-right">
            <Button size="sm" onClick={() => onGrant(r.name)}>
              <IconKey size={13} /> Grant
            </Button>
          </td>
        )}
      </>
    );
  }

  return (
    <TableWrap>
      <table className="tbl min-w-[980px]">
        <thead>{head}</thead>
        <tbody>
          {items.map((r) => (
            <tr key={r.name}>{row(r)}</tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}
