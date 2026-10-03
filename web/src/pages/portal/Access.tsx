import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { me, type Requestable } from "../../api";
import { ReasonDialog } from "../../components/Dialog";
import { SensitivityPill } from "../../components/pills";
import { Button, Card, Chips, Empty, PageHeader, Pill, Q, cx } from "../../components/ui";
import { AccessStatus, GrantList, Group, RequestList } from "../../components/portal/widgets";
import { minutes } from "../../lib/format";
import { useMe } from "./Home";

const PRESETS = [15, 30, 60, 120, 240, 480];

export function PortalAccess() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["me", "grants"], queryFn: me.grants, refetchInterval: 15_000 });
  const summary = useMe();
  const [req, setReq] = useState<Requestable | null>(null);
  const [mins, setMins] = useState(60);
  const s = summary.data;
  const paused = !!s && s.status.status !== "active";
  const grantRequests = (s?.requests ?? []).filter((r) => r.kind === "grant");
  const pending = new Set(grantRequests.filter((r) => r.status === "pending").map((r) => r.detail?.resource));
  const titles = Object.fromEntries((q.data?.requestable ?? []).map((r) => [r.name, r.title]));
  const max = req?.max_minutes ?? null;
  const tooLong = max !== null && mins > max;

  return (
    <div className="space-y-4">
      <PageHeader title="Access" subtitle="Time-boxed access to sensitive systems. You ask, an admin approves, and it ends on its own." />
      {s && paused && <AccessStatus s={s} compact />}

      <Card title="My grants" subtitle="Live grants count down; ended ones stay listed for a day" flush>
        <Q q={q} rows={3}>
          {(d) => <GrantList grants={d.grants} paused={paused} />}
        </Q>
      </Card>

      <Card title="Sensitive resources you can ask for">
        <Q q={q} rows={3}>
          {(d) =>
            d.requestable.length === 0 ? (
              <Empty title="Nothing to request" hint="Your company has not put any sensitive systems behind access requests." />
            ) : (
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {d.requestable.map((r) => {
                  const live = d.grants.find((g) => g.resource === r.name && g.live);
                  const auto = r.approval === "workflow";
                  return (
                    <div key={r.name} className="flex flex-col rounded-lg border border-line p-4">
                      <div className="flex items-start justify-between gap-2">
                        <div className="font-medium text-ink">{r.title}</div>
                        <SensitivityPill s={r.sensitivity} />
                      </div>
                      <div className="mt-0.5 truncate font-mono text-[11px] text-muted" title={r.urn}>
                        {r.urn}
                      </div>
                      <div className="mt-3 space-y-1.5 text-xs text-ink2">
                        <div className="flex gap-2">
                          <span className="w-20 shrink-0 text-muted">Lets you</span>
                          <Chips items={r.actions} empty="all actions" />
                        </div>
                        <div className="flex gap-2">
                          <span className="w-20 shrink-0 text-muted">At most</span>
                          {r.max_minutes ? minutes(r.max_minutes) : "no limit"} per grant
                        </div>
                        <div className="flex gap-2">
                          <span className="w-20 shrink-0 text-muted">Approval</span>
                          {auto ? "included automatically in workflows that use it; ask an admin otherwise" : "an admin approves each request"}
                        </div>
                      </div>
                      <div className="mt-auto flex flex-wrap items-center gap-2 pt-4">
                        {live ? (
                          <Pill tone="good" dot>
                            you have access now
                          </Pill>
                        ) : pending.has(r.name) ? (
                          <Pill tone="info" dot>
                            request pending
                          </Pill>
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
                  );
                })}
              </div>
            )
          }
        </Q>
        {paused && <p className="mt-3 text-xs text-muted">You can still send requests while your access is paused; grants only take effect once the review ends.</p>}
      </Card>

      <Card title="Your access requests" flush>
        {summary.isPending ? <div className="p-4 text-sm text-muted">Loading…</div> : <RequestList requests={grantRequests} titles={titles} empty="No access requests yet" />}
      </Card>

      <ReasonDialog
        open={!!req}
        onClose={() => setReq(null)}
        title={`Request ${req?.title ?? ""}`}
        description={req && <span>An admin reviews this. If approved, access starts right away and ends on its own after the time you pick.</span>}
        reasonLabel="What do you need it for?"
        reasonHint="The admin who decides sees this. Mention the ticket or incident if there is one."
        placeholder="e.g. Debugging INC-2231, need to read the orders table"
        confirmLabel="Send request"
        canConfirm={mins > 0 && !tooLong}
        onConfirm={async (reason) => {
          await me.request({ kind: "grant", resource: req!.name, minutes: mins, reason });
          await qc.invalidateQueries({ queryKey: ["me"] });
        }}
      >
        <Group label="For how long" hint={max ? `At most ${minutes(max)}. Ask for only what the task needs.` : undefined}>
          <div className="flex flex-wrap gap-1.5">
            {PRESETS.filter((p) => max === null || p <= max).map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => setMins(p)}
                aria-pressed={mins === p}
                className={cx(
                  "rounded-md px-3 py-1.5 text-sm ring-1 ring-inset",
                  mins === p ? "bg-accent/15 text-accent ring-accent/40" : "text-ink2 ring-line hover:bg-raised",
                )}
              >
                {minutes(p)}
              </button>
            ))}
          </div>
          <div className="mt-2 flex items-center gap-2">
            <input
              className="input tnum w-28"
              type="number"
              min={1}
              max={max ?? undefined}
              value={mins}
              onChange={(e) => setMins(Number(e.target.value))}
              aria-label="Minutes"
            />
            <span className="text-xs text-muted">minutes</span>
          </div>
          {tooLong && <span className="mt-1 block text-[11px] text-bad">That is longer than the {minutes(max)} maximum.</span>}
        </Group>
      </ReasonDialog>
    </div>
  );
}
