import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { me, type Workflow } from "../../api";
import { ReasonDialog } from "../../components/Dialog";
import { TierPill } from "../../components/pills";
import { Button, Chips, Empty, ErrorBox, Field, Loading, PageHeader, Pill } from "../../components/ui";
import { num } from "../../lib/format";
import { costRange, limits } from "../console/Workflows";
import { StatusBanner, useMe } from "./Home";

function WorkflowCard({ w, pending, onRequest }: { w: Workflow; pending: boolean; onRequest: () => void }) {
  return (
    <div className={`flex flex-col rounded-xl border bg-panel p-4 shadow-sm ${w.available ? "border-line" : "border-line/70"}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-semibold text-ink">{w.name}</div>
          <div className="mt-0.5 text-xs text-muted">{w.description || "—"}</div>
        </div>
        <TierPill tier={w.tier} />
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-muted">Typical cost / run</div>
          <div className="mt-0.5 text-sm font-medium text-ink">{costRange(w.measured)}</div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-muted">Runs (30d, org)</div>
          <div className="tnum mt-0.5 text-sm font-medium text-ink">{num(w.measured.runs)}</div>
        </div>
      </div>
      <div className="mt-3 space-y-1.5 text-xs">
        <div className="flex gap-2">
          <span className="w-14 shrink-0 text-muted">Limits</span>
          <Chips items={limits(w)} max={3} empty="none" />
        </div>
        <div className="flex gap-2">
          <span className="w-14 shrink-0 text-muted">Models</span>
          <Chips items={w.models} max={3} />
        </div>
        <div className="flex gap-2">
          <span className="w-14 shrink-0 text-muted">Tools</span>
          <Chips items={w.tools} max={3} />
        </div>
      </div>
      <div className="mt-auto flex flex-wrap items-center justify-between gap-2 pt-4">
        {w.available ? (
          <Pill tone="good" dot>
            available to you
          </Pill>
        ) : (
          <Pill tone={w.enabled ? "warn" : "neutral"} dot title={w.why ?? undefined}>
            {w.why ?? "not available"}
          </Pill>
        )}
        {!w.available && w.enabled && w.approval === "admin" && (
          pending ? (
            <Pill tone="info">request pending</Pill>
          ) : (
            <Button size="sm" variant="primary" onClick={onRequest}>
              Request access
            </Button>
          )
        )}
      </div>
    </div>
  );
}

export function PortalMenu() {
  const qc = useQueryClient();
  const q = useMe();
  const [wfReq, setWfReq] = useState<Workflow | null>(null);
  const [quota, setQuota] = useState(false);
  const [scale, setScale] = useState(2);

  if (q.isPending) return <Loading rows={8} />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const s = q.data;
  const pendingWf = new Set(s.requests.filter((r) => r.status === "pending" && r.kind === "workflow").map((r) => r.workflow));
  const quotaPending = s.requests.some((r) => r.status === "pending" && r.kind === "quota");
  const sorted = [...s.menu].sort((a, b) => Number(b.available) - Number(a.available));

  return (
    <div className="space-y-4">
      <PageHeader
        title="Workflow menu"
        subtitle="The kinds of AI work your company supports, what each costs per run, and what you can use."
        actions={
          quotaPending ? (
            <Pill tone="info">budget request pending</Pill>
          ) : (
            <Button onClick={() => setQuota(true)}>Request more budget</Button>
          )
        }
      />
      {s.status.status !== "active" && <StatusBanner s={s.status} />}
      <p className="text-xs text-muted">
        Label your calls with <code className="rounded bg-ink/[0.06] px-1 font-mono">x-acl-workflow</code> and{" "}
        <code className="rounded bg-ink/[0.06] px-1 font-mono">x-acl-task</code> headers to see what each task costs.
      </p>
      {sorted.length === 0 ? (
        <Empty title="No workflows on the menu yet" />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {sorted.map((w) => (
            <WorkflowCard key={w.name} w={w} pending={pendingWf.has(w.name)} onRequest={() => setWfReq(w)} />
          ))}
        </div>
      )}

      <ReasonDialog
        open={!!wfReq}
        onClose={() => setWfReq(null)}
        title={`Request ${wfReq?.name ?? ""}`}
        description={wfReq && <span>An admin reviews this. Typical cost per run: {costRange(wfReq.measured)}.</span>}
        reasonLabel="What do you need it for?"
        reasonHint="Seen by the admin who decides."
        placeholder="e.g. Release 2.0 soak test this week"
        confirmLabel="Send request"
        onConfirm={async (reason) => {
          await me.request({ kind: "workflow", workflow: wfReq!.name, reason });
          await qc.invalidateQueries({ queryKey: ["me"] });
        }}
      />
      <ReasonDialog
        open={quota}
        onClose={() => setQuota(false)}
        title="Request more daily budget"
        reasonLabel="Why do you need more?"
        reasonHint="Seen by the admin who decides."
        placeholder="e.g. Migrating the billing service this sprint"
        confirmLabel="Send request"
        onConfirm={async (reason) => {
          await me.request({ kind: "quota", scale, reason });
          await qc.invalidateQueries({ queryKey: ["me"] });
        }}
      >
        <Field label="Budget multiplier" hint="Relative to your normal daily budget (max 10×).">
          <div className="flex flex-wrap gap-1.5">
            {[1.5, 2, 3, 5].map((x) => (
              <button
                key={x}
                type="button"
                onClick={() => setScale(x)}
                className={`rounded-md px-3 py-1.5 text-sm ring-1 ring-inset ${scale === x ? "bg-accent/15 text-accent ring-accent/40" : "text-ink2 ring-line hover:bg-raised"}`}
              >
                {x}×
              </button>
            ))}
          </div>
        </Field>
      </ReasonDialog>
    </div>
  );
}
