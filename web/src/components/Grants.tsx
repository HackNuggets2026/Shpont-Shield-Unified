import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { admin, type Grant } from "../api";
import { ago, countdown, dateTime } from "../lib/format";
import { useNow } from "../lib/useNow";
import { ReasonDialog } from "./Dialog";
import { SensitivityPill } from "./pills";
import { Button, Chips, Empty, Field, Pill, TableWrap } from "./ui";

export function GrantsTable({
  grants,
  showPerson = true,
  onRevoke,
  empty = "No grants",
}: {
  grants: Grant[];
  showPerson?: boolean;
  onRevoke?: (g: Grant, reason: string) => Promise<unknown>;
  empty?: string;
}) {
  const now = useNow();
  const [revoking, setRevoking] = useState<Grant | null>(null);
  if (!grants.length) return <Empty title={empty} />;
  return (
    <>
      <TableWrap>
        <table className="tbl min-w-[720px]">
          <thead>
            <tr>
              <th>Resource</th>
              {showPerson && <th>Person</th>}
              <th>Actions</th>
              <th>Expires</th>
              <th>Granted</th>
              <th>Reason</th>
              {onRevoke && <th />}
            </tr>
          </thead>
          <tbody>
            {grants.map((g) => {
              const live = g.live && (!g.expires || g.expires > now);
              const soon = live && g.expires && g.expires - now < 600;
              return (
                <tr key={`${g.principal}-${g.id}`} className={live ? "" : "opacity-55"}>
                  <td>
                    <div className="font-medium text-ink">{g.title ?? g.resource}</div>
                    {g.title && g.title !== g.resource && <div className="font-mono text-[11px] text-muted">{g.resource}</div>}
                  </td>
                  {showPerson && (
                    <td>
                      {g.principal ? (
                        <Link className="hover:text-accent" to={`/console/people/${encodeURIComponent(g.principal)}`}>
                          {g.principal}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </td>
                  )}
                  <td>
                    <Chips items={g.actions} empty="all" />
                    {g.workflow && <div className="mt-0.5 text-[11px] text-muted">only in {g.workflow}</div>}
                  </td>
                  <td>
                    {live ? (
                      <Pill tone={soon ? "warn" : "good"} dot>
                        <span className="tnum">{countdown(g.expires, now)}</span>
                      </Pill>
                    ) : (
                      <Pill tone="neutral">expired {ago(g.expires, now)}</Pill>
                    )}
                  </td>
                  <td className="text-xs" title={dateTime(g.granted_at)}>
                    <div>{ago(g.granted_at, now)}</div>
                    <div className="text-muted">by {g.granted_by}</div>
                  </td>
                  <td className="max-w-[240px] text-xs text-ink2">
                    <span className="line-clamp-2" title={g.reason}>
                      {g.reason}
                    </span>
                  </td>
                  {onRevoke && (
                    <td className="text-right">
                      {live && (
                        <Button size="sm" variant="secondary" onClick={() => setRevoking(g)}>
                          Revoke
                        </Button>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableWrap>
      {onRevoke && (
        <ReasonDialog
          open={!!revoking}
          onClose={() => setRevoking(null)}
          title={`Revoke ${revoking?.title ?? revoking?.resource ?? ""}`}
          description={
            <>
              <b>{revoking?.principal}</b> loses access immediately. The grant stays in the record as expired.
            </>
          }
          confirmLabel="Revoke access"
          tone="danger"
          onConfirm={(reason) => onRevoke(revoking!, reason)}
        />
      )}
    </>
  );
}

/** Admin: give a person time-boxed access to an access_grant resource. */
export function GrantDialog({ open, onClose, principal }: { open: boolean; onClose: () => void; principal?: string }) {
  const qc = useQueryClient();
  const catalog = useQuery({ queryKey: ["admin", "catalog"], queryFn: admin.catalog, enabled: open });
  const people = useQuery({ queryKey: ["admin", "principals"], queryFn: admin.principals, enabled: open && !principal });
  const menu = useQuery({ queryKey: ["admin", "menu"], queryFn: admin.menu, enabled: open });
  const resources = useMemo(() => catalog.data?.classes.access_grant ?? [], [catalog.data]);

  const [who, setWho] = useState(principal ?? "");
  const [res, setRes] = useState("");
  const [mins, setMins] = useState(60);
  const [actions, setActions] = useState<string[]>([]);
  const [wf, setWf] = useState("");

  useEffect(() => {
    if (open) {
      setWho(principal ?? "");
      setWf("");
    }
  }, [open, principal]);
  useEffect(() => {
    if (!res && resources.length) setRes(resources[0].name);
  }, [resources, res]);
  const r = resources.find((x) => x.name === res);
  useEffect(() => {
    setActions(r?.actions ?? []);
    if (r?.grant) setMins((m) => Math.min(m, r.grant!.max_minutes));
  }, [r]);

  return (
    <ReasonDialog
      open={open}
      onClose={onClose}
      title={principal ? `Grant access to ${principal}` : "Grant access"}
      confirmLabel="Grant access"
      canConfirm={!!who && !!res && mins > 0}
      onConfirm={async (reason) => {
        await admin.grant(who, { resource: res, minutes: mins, actions, workflow: wf || undefined, reason });
        await qc.invalidateQueries({ queryKey: ["admin"] });
      }}
    >
      {!principal && (
        <Field label="Person">
          <select className="input" value={who} onChange={(e) => setWho(e.target.value)}>
            <option value="">Choose…</option>
            {(people.data ?? []).map((p) => (
              <option key={p.principal} value={p.principal}>
                {p.principal} ({p.team})
              </option>
            ))}
          </select>
        </Field>
      )}
      <Field label="Resource">
        {resources.length === 0 && !catalog.isPending ? (
          <div className="text-xs text-muted">No access_grant resources in the catalog.</div>
        ) : (
          <select className="input" value={res} onChange={(e) => setRes(e.target.value)}>
            {resources.map((x) => (
              <option key={x.name} value={x.name}>
                {x.title || x.name} · {x.sensitivity}
              </option>
            ))}
          </select>
        )}
      </Field>
      {r && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <span className="font-mono">{r.urn}</span>
          <SensitivityPill s={r.sensitivity} />
          {r.owner && <span>owner {r.owner}</span>}
        </div>
      )}
      <div className="grid grid-cols-2 gap-3">
        <Field label="Minutes" hint={r?.grant ? `max ${r.grant.max_minutes} min` : undefined}>
          <input
            className="input tnum"
            type="number"
            min={1}
            max={r?.grant?.max_minutes}
            value={mins}
            onChange={(e) => setMins(Number(e.target.value))}
          />
        </Field>
        <Field label="Only within workflow" hint="optional">
          <select className="input" value={wf} onChange={(e) => setWf(e.target.value)}>
            <option value="">any workflow</option>
            {(menu.data?.workflows ?? []).map((w) => (
              <option key={w.name} value={w.name}>
                {w.name}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {r && r.actions.length > 0 && (
        <Field label="Actions">
          <div className="flex flex-wrap gap-3">
            {r.actions.map((a) => (
              <label key={a} className="flex items-center gap-1.5 text-sm text-ink2">
                <input
                  type="checkbox"
                  className="accent-[rgb(var(--accent))]"
                  checked={actions.includes(a)}
                  onChange={(e) => setActions((x) => (e.target.checked ? [...x, a] : x.filter((y) => y !== a)))}
                />
                {a}
              </label>
            ))}
          </div>
        </Field>
      )}
    </ReasonDialog>
  );
}
