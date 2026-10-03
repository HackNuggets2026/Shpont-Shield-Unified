import { useState } from "react";
import { Link } from "react-router-dom";
import type { Lease } from "../api";
import { ago, dateTime, minutes, usd } from "../lib/format";
import { Button, Empty, ErrorBox, Pill, TableWrap } from "./ui";
import { IconStop } from "./icons";

/** Running (or recent) resource leases with a Stop button; zombie flags are highlighted. */
export function LeasesTable({
  leases,
  onStop,
  showPerson = true,
  closed = false,
  empty = "Nothing running",
}: {
  leases: Lease[];
  onStop?: (id: string) => Promise<{ ok: boolean; stopped: boolean }>;
  showPerson?: boolean;
  closed?: boolean;
  empty?: string;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ id: string; text: string } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const stop = async (id: string) => {
    if (!onStop) return;
    setBusy(id);
    setError(null);
    try {
      const r = await onStop(id);
      setMsg({ id, text: r.stopped ? "Stopped." : "Lease closed; the gateway could not stop the resource itself." });
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
      {msg && <div className="border-b border-line bg-good/10 px-3 py-2 text-xs text-good">{msg.text}</div>}
      {!leases.length ? (
        <Empty title={empty} />
      ) : (
      <TableWrap>
        <table className="tbl min-w-[720px]">
          <thead>
            <tr>
              <th>Resource</th>
              {showPerson && <th>Person</th>}
              <th>Workflow / task</th>
              <th>{closed ? "Ended" : "Started"}</th>
              <th className="text-right">{closed ? "Cost" : "Running"}</th>
              <th>{closed ? "Why" : "Idle"}</th>
              {!closed && onStop && <th />}
            </tr>
          </thead>
          <tbody>
            {leases.map((l) => {
              const zombie = l.flags.length > 0;
              return (
                <tr key={l.id} className={zombie ? "bg-warn/[0.06]" : ""}>
                  <td>
                    <div className="font-medium text-ink">{l.resource}</div>
                    <div className="font-mono text-[11px] text-muted">{l.handle ?? l.id}</div>
                  </td>
                  {showPerson && (
                    <td>
                      <Link className="hover:text-accent" to={`/console/people/${encodeURIComponent(l.principal)}`}>
                        {l.principal}
                      </Link>
                      <div className="text-[11px] text-muted">{l.team}</div>
                    </td>
                  )}
                  <td className="text-xs">
                    <div>{l.workflow ?? <span className="text-muted">—</span>}</div>
                    {l.task && <div className="font-mono text-[11px] text-muted">{l.task}</div>}
                  </td>
                  <td className="text-xs" title={dateTime(closed ? l.ended : l.started)}>
                    {ago(closed ? l.ended : l.started)}
                  </td>
                  <td className="tnum text-right text-xs">
                    {closed ? usd(l.usd) : (
                      <>
                        <div>{minutes(l.minutes)}</div>
                        <div className="text-muted">{usd(l.running_usd)}</div>
                      </>
                    )}
                  </td>
                  <td className="text-xs">
                    {closed ? (
                      <span className="text-muted">{l.end_reason ?? "—"}</span>
                    ) : (
                      <div className="flex flex-wrap items-center gap-1">
                        <span className="tnum">{minutes(l.idle_minutes)}</span>
                        {l.flags.map((f) => (
                          <Pill key={f} tone="warn" dot>
                            zombie: {f}
                          </Pill>
                        ))}
                      </div>
                    )}
                  </td>
                  {!closed && onStop && (
                    <td className="text-right">
                      <Button size="sm" variant={zombie ? "danger" : "secondary"} onClick={() => stop(l.id)} disabled={busy === l.id}>
                        <IconStop size={13} />
                        {busy === l.id ? "Stopping…" : "Stop"}
                      </Button>
                    </td>
                  )}
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
