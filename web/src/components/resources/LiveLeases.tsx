import { useState } from "react";
import { Logo } from "../Logo";
import { Link } from "react-router-dom";
import type { CatalogItem, Lease } from "../../api";
import type { OrgFields } from "../../opsApi";
import { OrgLine } from "../opsKit";
import { ago, dateTime, minutes, usd } from "../../lib/format";
import { useNow } from "../../lib/useNow";
import { unitUsd } from "./catalogInfo";
import { Button, Empty, ErrorBox, Meter, Pill, TableWrap, cx } from "../ui";
import { IconStop } from "../icons";

/**
 * Open leases, ticking between polls: running minutes, idle minutes and cost move every second from the last server
 * snapshot. Idle time is shown against the resource's zombie threshold, so a lease about to be reclaimed is visible.
 */
export function LiveLeases({
  leases,
  fetchedAt,
  catalog,
  onStop,
}: {
  leases: (Lease & OrgFields)[];
  fetchedAt: number; // ms, when `leases` was fetched
  catalog: Record<string, CatalogItem>;
  onStop: (id: string) => Promise<{ ok: boolean; stopped: boolean }>;
}) {
  const now = useNow();
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const drift = Math.max(0, now - fetchedAt / 1000) / 60; // minutes since the snapshot

  const stop = async (l: Lease, label: string) => {
    setBusy(l.id);
    setError(null);
    setMsg(null);
    try {
      const r = await onStop(l.id);
      setMsg(
        r.stopped
          ? `Stopped ${label} for ${l.principal}.`
          : `Closed ${l.principal}'s lease on ${label}; billing stopped, but the gateway could not shut the resource down itself.`,
      );
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      {error != null && (
        <div className="p-3">
          <ErrorBox error={error} compact />
        </div>
      )}
      {msg && <div className="border-b border-line bg-good/10 px-4 py-2 text-xs text-good">{msg}</div>}
      {leases.length === 0 ? (
        <Empty title="Nothing running" hint="Leases appear here the moment an agent boots a simulator or creates a VM." />
      ) : (
      <TableWrap>
        <table className="tbl min-w-[860px]">
          <thead>
            <tr>
              <th>Person</th>
              <th>Resource</th>
              <th>Workflow / task</th>
              <th className="text-right">Running</th>
              <th>Idle</th>
              <th className="text-right">Cost so far</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {leases.map((l) => {
              const r = catalog[l.resource];
              const title = r?.title || l.resource;
              const rate = r?.price.usd_per_unit ?? 0;
              const running = (l.minutes ?? (fetchedAt / 1000 - l.started) / 60) + drift;
              const idle = (l.idle_minutes ?? 0) + drift;
              const cost = (l.running_usd ?? 0) + drift * rate;
              const limit = r?.lease?.idle_minutes ?? null;
              const zombie = l.flags.length > 0;
              const left = limit != null ? limit - idle : null;
              const near = !zombie && left != null && left <= limit! * 0.3;
              return (
                <tr key={l.id} className={cx(zombie && "bg-warn/[0.08]")}>
                  <td>
                    <Link className="font-medium hover:text-accent" to={`/console/people/${encodeURIComponent(l.principal)}`}>
                      {l.name || l.principal}
                    </Link>
                    <OrgLine team={l.team} department={l.department} />
                  </td>
                  <td>
                    <div className="flex items-center gap-2 font-medium text-ink">
                      <Logo id={l.resource} label={title} />
                      {title}
                    </div>
                    <div className="font-mono text-[11px] text-muted">{l.handle ?? l.id}</div>
                  </td>
                  <td className="text-xs">
                    <div>{l.workflow ?? <span className="text-muted">no workflow</span>}</div>
                    {l.task && <div className="font-mono text-[11px] text-muted">{l.task}</div>}
                  </td>
                  <td className="tnum text-right text-xs" title={`started ${dateTime(l.started)}`}>
                    <div className="font-medium text-ink">{minutes(running)}</div>
                    <div className="text-muted">since {ago(l.started, now)}</div>
                  </td>
                  <td className="min-w-[180px] text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="tnum">
                        {minutes(idle)}
                        {limit != null && <span className="text-muted"> / {limit} min</span>}
                      </span>
                      {zombie ? (
                        <Pill tone="warn" dot title="Idle past the resource's limit">
                          zombie: {l.flags.join(", ")}
                        </Pill>
                      ) : near ? (
                        <span className="text-warn">zombie in {minutes(Math.max(left!, 0))}</span>
                      ) : null}
                    </div>
                    {limit != null && (
                      <div className="mt-1">
                        <Meter value={idle} max={limit} tone={zombie ? "bad" : near ? "warn" : "accent"} />
                      </div>
                    )}
                    {zombie && (
                      <div className="mt-1 text-[11px] text-muted">
                        {r?.lease?.auto_reclaim ? "auto-reclaim in progress" : "auto-reclaim off: stop it by hand"}
                      </div>
                    )}
                  </td>
                  <td className="tnum text-right text-xs">
                    <div className="font-medium text-ink">{usd(cost)}</div>
                    {rate > 0 && <div className="text-muted">{unitUsd(rate)}/min</div>}
                  </td>
                  <td className="text-right">
                    <Button size="sm" variant={zombie ? "danger" : "secondary"} onClick={() => stop(l, title)} disabled={busy === l.id}>
                      <IconStop size={13} />
                      {busy === l.id ? "Stopping…" : "Stop"}
                    </Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableWrap>
      )}
    </div>
  );
}

/** Leases the gateway reclaimed recently, so an auto-reclaim does not vanish silently from the table above. */
export function RecentReclaims({ recent, catalog, withinMin = 30 }: { recent: Lease[]; catalog: Record<string, CatalogItem>; withinMin?: number }) {
  const now = useNow(5000);
  const rows = recent.filter((l) => l.ended && now - l.ended < withinMin * 60 && (l.end_reason ?? "").startsWith("reclaimed"));
  if (!rows.length) return null;
  return (
    <ul className="divide-y divide-line border-t border-line bg-warn/[0.05]">
      {rows.map((l) => (
        <li key={l.id} className="flex flex-wrap items-center gap-x-2 gap-y-1 px-4 py-2 text-xs">
          <Pill tone="warn">auto-reclaimed</Pill>
          <span className="text-ink">
            <b>{(l as Lease & OrgFields).name || l.principal}</b>'s {catalog[l.resource]?.title ?? l.resource} <span className="font-mono text-muted">{l.handle}</span>
          </span>
          <span className="text-muted">
            {l.end_reason?.replace(/^reclaimed: ?/, "") || "zombie"} · {usd(l.usd)} total · {ago(l.ended, now)}
          </span>
        </li>
      ))}
    </ul>
  );
}
