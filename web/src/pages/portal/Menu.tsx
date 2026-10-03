import { useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { me, type Breakdown, type MeSummary, type Workflow } from "../../api";
import { ReasonDialog } from "../../components/Dialog";
import { TierPill } from "../../components/pills";
import { Button, Card, Chips, Empty, ErrorBox, Loading, PageHeader, Pill, cx, type Tone } from "../../components/ui";
import { AccessStatus, Group, RequestList } from "../../components/portal/widgets";
import { minutes, num, pct, tokens, usd } from "../../lib/format";
import { useMe } from "./Home";

/** "about 34¢" / "about $2.50": costs read better in cents below a dollar. */
function money(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v < 0.01) return "under 1¢";
  if (v < 1) return `${Math.round(v * 100)}¢`;
  return usd(v);
}

function CostLine({ w }: { w: Workflow }) {
  const m = w.measured;
  if (!m.runs) return <div className="text-sm text-muted">No runs measured yet, so no typical cost.</div>;
  return (
    <div>
      <div className="text-sm text-ink">
        Usually <span className="font-semibold">{money(m.usd_p50)}</span> a run
        {m.minutes_p50 ? <span className="text-ink2"> · about {minutes(m.minutes_p50)}</span> : null}
      </div>
      <div className="mt-0.5 text-xs text-muted">
        1 in 10 runs costs more than {money(m.usd_p90)} · measured over {num(m.runs)} runs across the company in 30 days
      </div>
    </div>
  );
}

function limitsText(w: Workflow): string[] {
  const out: string[] = [];
  const caps = [w.per_run.usd !== null ? usd(w.per_run.usd) : null, w.per_run.tokens !== null ? `${tokens(w.per_run.tokens)} tokens` : null].filter(Boolean);
  if (caps.length) out.push(`Stops at ${caps.join(" or ")} per run`);
  for (const [r, l] of Object.entries(w.resources)) {
    const bits = [l.max_concurrent !== null ? `up to ${l.max_concurrent} at a time` : null, l.max_minutes !== null ? `${minutes(l.max_minutes)} each` : null].filter(Boolean);
    out.push(bits.length ? `${r}: ${bits.join(", ")}` : `includes ${r.replace(/_/g, " ")}`);
  }
  return out;
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-2">
      <span className="w-16 shrink-0 text-muted">{label}</span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

type Avail = { tone: Tone; label: string; request: boolean };

function availability(w: Workflow, s: MeSummary, pending: boolean): Avail {
  if (s.status.status !== "active") return { tone: "neutral", label: `paused while your access is ${s.status.status}`, request: false };
  if (!w.enabled) return { tone: "neutral", label: "switched off for everyone right now", request: false };
  if (w.available) {
    return w.approval === "admin" && s.status.approved_workflows.includes(w.name)
      ? { tone: "good", label: "approved for you", request: false }
      : { tone: "good", label: "available to you", request: false };
  }
  const why = w.why ?? "not available";
  if (why === "needs admin approval") return { tone: "warn", label: pending ? "request pending" : "needs an admin's approval", request: !pending };
  const team = /^not available to team '(.+)'$/.exec(why);
  if (team) return { tone: "neutral", label: `not offered to the ${team[1]} team`, request: false };
  const role = /^not available to role '(.+)'$/.exec(why);
  if (role) return { tone: "neutral", label: `not offered to your role (${role[1]})`, request: false };
  return { tone: "neutral", label: why, request: false };
}

function WorkflowCard({ w, s, mine, pending, onRequest }: { w: Workflow; s: MeSummary; mine?: Breakdown; pending: boolean; onRequest: () => void }) {
  const a = availability(w, s, pending);
  const limits = limitsText(w);
  return (
    <div className={cx("flex flex-col rounded-xl border bg-panel p-4 shadow-sm", a.tone === "good" ? "border-line" : "border-line/70")}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-semibold text-ink">{w.name.replace(/_/g, " ")}</div>
          <div className="mt-0.5 text-sm text-ink2">{w.description ? w.description[0].toUpperCase() + w.description.slice(1) : "—"}</div>
        </div>
        <TierPill tier={w.tier} />
      </div>
      <div className="mt-3 rounded-lg bg-raised/60 px-3 py-2.5">
        <CostLine w={w} />
        {mine && (mine.usd || mine.requests) ? (
          <div className="mt-1.5 text-xs text-ink2">
            You this week: {usd(Number(mine.usd))} over {num(Number(mine.requests))} calls
          </div>
        ) : null}
      </div>
      <div className="mt-3 space-y-1.5 text-xs">
        <Row label="Limits">{limits.length ? <span className="text-ink2">{limits.join(" · ")}</span> : <span className="text-muted">none per run</span>}</Row>
        <Row label="Models">
          <Chips items={w.models} max={3} empty="any approved model" />
        </Row>
        <Row label="Tools">
          <Chips items={w.tools} max={4} empty="none" />
        </Row>
      </div>
      <div className="mt-auto flex flex-wrap items-center justify-between gap-2 pt-4">
        <Pill tone={a.tone} dot>
          {a.label}
        </Pill>
        {a.request && (
          <Button size="sm" variant="primary" onClick={onRequest}>
            Request access
          </Button>
        )}
      </div>
    </div>
  );
}

const SCALES = [1.5, 2, 3, 5];

export function PortalMenu() {
  const qc = useQueryClient();
  const q = useMe();
  const grants = useQuery({ queryKey: ["me", "grants"], queryFn: me.grants, staleTime: 60_000 });
  const [wfReq, setWfReq] = useState<Workflow | null>(null);
  const [quota, setQuota] = useState(false);
  const [scale, setScale] = useState(2);

  if (q.isPending) return <Loading rows={8} />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const s = q.data;
  const pendingWf = new Set(s.requests.filter((r) => r.status === "pending" && r.kind === "workflow").map((r) => r.workflow));
  const quotaPending = s.requests.some((r) => r.status === "pending" && r.kind === "quota");
  const rank = (w: Workflow) => (availability(w, s, false).tone === "good" ? 0 : w.why === "needs admin approval" ? 1 : 2);
  const sorted = [...s.menu].sort((a, b) => rank(a) - rank(b));
  const mineBy = Object.fromEntries(s.by_workflow.map((r) => [String(r.workflow), r]));
  const titles = Object.fromEntries((grants.data?.requestable ?? []).map((r) => [r.name, r.title]));
  const mine = s.budgets.find((b) => b.scope === "principal");
  const paused = s.status.status !== "active";

  return (
    <div className="space-y-4">
      <PageHeader
        title="Workflow menu"
        subtitle="The kinds of AI work the company supports, what a typical run costs, and what you can use."
        actions={
          quotaPending ? (
            <Pill tone="info">budget request pending</Pill>
          ) : (
            <Button onClick={() => setQuota(true)}>Request more budget</Button>
          )
        }
      />
      {paused && <AccessStatus s={s} compact />}
      {sorted.length === 0 ? (
        <Card>
          <Empty title="No workflows on the menu yet" hint="Your admins have not published any. Everyday chat still works within your budget." />
        </Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {sorted.map((w) => (
            <WorkflowCard key={w.name} w={w} s={s} mine={mineBy[w.name]} pending={pendingWf.has(w.name)} onRequest={() => setWfReq(w)} />
          ))}
        </div>
      )}
      <p className="text-xs text-muted">
        Tip: label your calls with the <code className="rounded bg-ink/[0.06] px-1 font-mono">x-acl-workflow</code> and{" "}
        <code className="rounded bg-ink/[0.06] px-1 font-mono">x-acl-task</code> headers so My activity can show what each task cost.
      </p>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Your requests" subtitle="Workflows, budget and access you asked for, and what was decided" flush>
          <RequestList requests={s.requests} titles={titles} />
        </Card>
        <Card title="Need a bigger daily budget?">
          <p className="text-sm text-ink2">
            Your budget is {pct(s.status.budget_scale, 0)} of normal
            {mine?.tokens_limit ? ` (${tokens(mine.tokens_limit)} tokens a day)` : ""}
            {mine?.usd_limit ? ` (${usd(mine.usd_limit)} a day)` : ""}. If a project needs more for a while, say why and an admin decides.
          </p>
          <div className="mt-3">
            {quotaPending ? (
              <Pill tone="info" dot>
                a budget request is already waiting
              </Pill>
            ) : (
              <Button variant="primary" onClick={() => setQuota(true)}>
                Request more budget
              </Button>
            )}
          </div>
        </Card>
      </div>

      <ReasonDialog
        open={!!wfReq}
        onClose={() => setWfReq(null)}
        title={`Request ${wfReq?.name.replace(/_/g, " ") ?? ""}`}
        description={
          wfReq && (
            <span>
              An admin reviews this.{" "}
              {wfReq.measured.runs ? `A typical run costs ${money(wfReq.measured.usd_p50)}, up to ${money(wfReq.measured.usd_p90)} for big ones.` : ""}
            </span>
          )
        }
        reasonLabel="What do you need it for?"
        reasonHint="The admin who decides sees this. You will see their answer here."
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
        reasonHint="The admin who decides sees this. You will see their answer under Your requests."
        placeholder="e.g. Migrating the billing service this sprint"
        confirmLabel="Send request"
        onConfirm={async (reason) => {
          await me.request({ kind: "quota", scale, reason });
          await qc.invalidateQueries({ queryKey: ["me"] });
        }}
      >
        <Group label="How much budget" hint="Relative to the normal daily budget for your role (at most 10×).">
          <div className="flex flex-wrap gap-1.5">
            {SCALES.map((x) => (
              <button
                key={x}
                type="button"
                onClick={() => setScale(x)}
                aria-pressed={scale === x}
                className={cx(
                  "rounded-md px-3 py-1.5 text-sm ring-1 ring-inset",
                  scale === x ? "bg-accent/15 text-accent ring-accent/40" : "text-ink2 ring-line hover:bg-raised",
                )}
              >
                {x}×
              </button>
            ))}
          </div>
        </Group>
      </ReasonDialog>
    </div>
  );
}
