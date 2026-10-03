import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { me, type Requestable } from "../../api";
import { ReasonDialog } from "../../components/Dialog";
import { GrantsTable } from "../../components/Grants";
import { SensitivityPill } from "../../components/pills";
import { Button, Card, Chips, Empty, Field, PageHeader, Pill, Q } from "../../components/ui";
import { minutes } from "../../lib/format";
import { useMe } from "./Home";

export function PortalAccess() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["me", "grants"], queryFn: me.grants, refetchInterval: 15_000 });
  const summary = useMe();
  const [req, setReq] = useState<Requestable | null>(null);
  const [mins, setMins] = useState(60);
  const pending = new Set(
    (summary.data?.requests ?? []).filter((r) => r.status === "pending" && r.kind === "grant").map((r) => r.detail?.resource),
  );

  return (
    <div className="space-y-4">
      <PageHeader title="Access" subtitle="Time-boxed access to sensitive systems. Grants expire on their own." />
      <Card title="My grants" flush>
        <Q q={q} rows={3}>
          {(d) => (
            <GrantsTable
              grants={[...d.grants].sort((a, b) => Number(b.live) - Number(a.live))}
              showPerson={false}
              empty="You hold no grants"
            />
          )}
        </Q>
      </Card>
      <Card title="Resources you can request">
        <Q q={q} rows={3}>
          {(d) =>
            d.requestable.length === 0 ? (
              <Empty title="Nothing to request" />
            ) : (
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {d.requestable.map((r) => (
                  <div key={r.name} className="flex flex-col rounded-lg border border-line p-4">
                    <div className="flex items-start justify-between gap-2">
                      <div className="font-medium text-ink">{r.title}</div>
                      <SensitivityPill s={r.sensitivity} />
                    </div>
                    <div className="mt-0.5 truncate font-mono text-[11px] text-muted" title={r.urn}>
                      {r.urn}
                    </div>
                    <div className="mt-3 space-y-1 text-xs text-ink2">
                      <div className="flex gap-2">
                        <span className="w-20 text-muted">Actions</span>
                        <Chips items={r.actions} empty="all" />
                      </div>
                      <div className="flex gap-2">
                        <span className="w-20 text-muted">Max</span>
                        {r.max_minutes ? minutes(r.max_minutes) : "—"}
                      </div>
                      <div className="flex gap-2">
                        <span className="w-20 text-muted">Approval</span>
                        {r.approval === "workflow" ? "automatic inside an allowed workflow" : "an admin"}
                      </div>
                    </div>
                    <div className="mt-auto pt-4">
                      {pending.has(r.name) ? (
                        <Pill tone="info">request pending</Pill>
                      ) : (
                        <Button
                          size="sm"
                          variant="primary"
                          onClick={() => {
                            setMins(Math.min(60, r.max_minutes ?? 60));
                            setReq(r);
                          }}
                        >
                          Request access
                        </Button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )
          }
        </Q>
      </Card>

      <ReasonDialog
        open={!!req}
        onClose={() => setReq(null)}
        title={`Request ${req?.title ?? ""}`}
        reasonLabel="What do you need it for?"
        reasonHint="Seen by the admin who decides; the grant ends on its own."
        placeholder="e.g. Debugging INC-2231, need to read the orders table"
        confirmLabel="Send request"
        canConfirm={mins > 0}
        onConfirm={async (reason) => {
          await me.request({ kind: "grant", resource: req!.name, minutes: mins, reason });
          await qc.invalidateQueries({ queryKey: ["me"] });
        }}
      >
        <Field label="For how long (minutes)" hint={req?.max_minutes ? `At most ${minutes(req.max_minutes)}.` : undefined}>
          <input className="input tnum" type="number" min={1} max={req?.max_minutes ?? undefined} value={mins} onChange={(e) => setMins(Number(e.target.value))} />
        </Field>
      </ReasonDialog>
    </div>
  );
}
