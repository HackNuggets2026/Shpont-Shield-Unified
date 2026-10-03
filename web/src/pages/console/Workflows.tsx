import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { admin, type Workflow } from "../../api";
import { ReasonDialog } from "../../components/Dialog";
import { TierPill } from "../../components/pills";
import { Card, Empty, PageHeader, Pill, Q, Stat, TableWrap, Toggle } from "../../components/ui";
import { num, tokens, usd } from "../../lib/format";
import { useOutputByWorkflow } from "../../lib/productivity";

interface Pending {
  wf: Workflow;
  patch: Record<string, unknown>;
  what: string;
}

export function costRange(m: Workflow["measured"]) {
  if (!m.runs) return <span className="text-muted">no runs yet</span>;
  return (
    <span className="tnum whitespace-nowrap">
      {usd(m.usd_p50)}
      <span className="text-muted"> – </span>
      {usd(m.usd_p90)}
    </span>
  );
}

export function limits(w: Workflow) {
  const parts: string[] = [];
  if (w.per_run.usd !== null) parts.push(`${usd(w.per_run.usd)}/run`);
  if (w.per_run.tokens !== null) parts.push(`${tokens(w.per_run.tokens)} tok/run`);
  for (const [r, l] of Object.entries(w.resources)) {
    const bits = [l.max_concurrent !== null ? `×${l.max_concurrent}` : null, l.max_minutes !== null ? `${l.max_minutes}m` : null].filter(Boolean);
    parts.push(bits.length ? `${r} ${bits.join(" ")}` : r);
  }
  return parts;
}

/** One labelled line of chips that never wraps; the full list is in the tooltip. */
function ScopeLine({ label, items, empty }: { label: string; items: string[]; empty: string }) {
  return (
    <div className="flex items-center gap-2 py-px text-[11px]" title={items.join(", ") || empty}>
      <span className="w-11 shrink-0 text-muted">{label}</span>
      <span className="flex min-w-0 items-center gap-1 overflow-hidden">
        {items.length === 0 ? (
          <span className="text-muted">{empty}</span>
        ) : (
          <>
            {items.slice(0, 2).map((t) => (
              <span key={t} className="truncate whitespace-nowrap rounded bg-ink/[0.06] px-1.5 font-mono text-ink2">
                {t}
              </span>
            ))}
            {items.length > 2 && <span className="shrink-0 text-muted">+{items.length - 2}</span>}
          </>
        )}
      </span>
    </div>
  );
}

export function Workflows() {
  const qc = useQueryClient();
  const menu = useQuery({ queryKey: ["admin", "menu"], queryFn: admin.menu, refetchInterval: 30_000 });
  const spend = useQuery({ queryKey: ["admin", "timeseries", "workflow", 30], queryFn: () => admin.timeseries("workflow", 30) });
  const output = useOutputByWorkflow(30);
  const [pending, setPending] = useState<Pending | null>(null);
  const edit = useMutation({
    mutationFn: ({ wf, patch, reason }: Pending & { reason: string }) => admin.editWorkflow(wf.name, patch, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "menu"] }),
  });

  const spend30 = useMemo(() => {
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(spend.data?.series ?? {})) out[k] = v.reduce((a, b) => a + b, 0);
    return out;
  }, [spend.data]);

  return (
    <div>
      <PageHeader
        title="Workflows"
        subtitle="The priced menu of AI work: what each kind of task is allowed to use and what a run really costs."
      />
      <Q q={menu} rows={8}>
        {(m) => (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 rounded-xl border border-line bg-panel p-4 sm:grid-cols-4">
              <Stat label="Workflows" value={`${m.workflows.filter((w) => w.enabled).length} enabled / ${m.workflows.length}`} />
              <Stat label="Need approval" value={m.workflows.filter((w) => w.approval !== "none").length} />
              <Stat label="Labels required" value={m.require_label ? "Yes, unlabeled calls blocked" : "No"} />
              <Stat label="Unlabeled traffic" value={m.classify_unlabeled ? "Auto-classified" : "Left unlabeled"} />
            </div>
            <Card flush title="Menu" subtitle="Cost per run is measured over the last 30 days (p50 – p90)">
              {m.workflows.length === 0 ? (
                <Empty title="No workflows on the menu" hint="Add workflows under menu.workflows in policy.yaml." />
              ) : (
                <TableWrap>
                  <table className="tbl min-w-[1040px]">
                    <thead>
                      <tr>
                        <th>Workflow</th>
                        <th>Tier</th>
                        <th className="text-right">Cost / run</th>
                        <th className="text-right">Runs</th>
                        <th className="text-right">Spend 30d</th>
                        <th className="text-right" title="All AI spend in the workflow over 30 days, divided by the commits Claude Code reported in it">
                          AI $ / commit
                        </th>
                        <th>Scope</th>
                        <th className="text-center">Approval</th>
                        <th className="text-center">Enabled</th>
                      </tr>
                    </thead>
                    <tbody>
                      {m.workflows.map((w) => (
                        <tr key={w.name} className={w.enabled ? "" : "opacity-60"}>
                          <td className="max-w-[220px]">
                            <div className="font-medium text-ink">{w.name}</div>
                            <div className="truncate text-xs text-muted" title={w.description}>
                              {w.description || "—"}
                            </div>
                          </td>
                          <td>
                            <TierPill tier={w.tier} />
                          </td>
                          <td className="text-right">{costRange(w.measured)}</td>
                          <td className="tnum text-right">{num(w.measured.runs)}</td>
                          <td className="tnum text-right">{usd(spend30[w.name] ?? 0)}</td>
                          <td className="tnum text-right">
                            {(() => {
                              const o = output.data?.[w.name];
                              if (!o?.commits) return <span className="text-muted">—</span>;
                              return (
                                <span title={`${num(o.commits)} commits · ${num(o.prs)} PRs · ${num(o.linesAdded)} lines added`}>
                                  {usd((spend30[w.name] ?? 0) / o.commits)}
                                  <div className="text-[11px] text-muted">{num(o.commits)} commits</div>
                                </span>
                              );
                            })()}
                          </td>
                          <td className="min-w-[260px] max-w-[320px]">
                            <ScopeLine label="limits" items={limits(w)} empty="none" />
                            <ScopeLine label="who" items={[...w.teams, ...w.roles.map((r) => `role:${r}`)]} empty="everyone" />
                            <ScopeLine label="models" items={w.models} empty="any" />
                            <ScopeLine label="tools" items={w.tools} empty="any" />
                          </td>
                          <td>
                            <div className="flex items-center justify-center gap-2">
                              <Toggle
                                label={`${w.name} requires admin approval`}
                                checked={w.approval === "admin"}
                                onChange={(v) =>
                                  setPending({ wf: w, patch: { approval: v ? "admin" : "none" }, what: v ? "Require approval" : "Remove approval" })
                                }
                              />
                              <span className="w-9 text-xs text-muted">{w.approval === "admin" ? "admin" : "none"}</span>
                            </div>
                          </td>
                          <td className="text-center">
                            <Toggle
                              label={`${w.name} enabled`}
                              checked={w.enabled}
                              onChange={(v) => setPending({ wf: w, patch: { enabled: v }, what: v ? "Enable" : "Disable" })}
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableWrap>
              )}
            </Card>
            <p className="text-xs text-muted">
              Changes are written to the admin policy overlay, validated and hot-reloaded; a rejected change shows the validator's message.
            </p>
          </div>
        )}
      </Q>
      <ReasonDialog
        open={!!pending}
        onClose={() => setPending(null)}
        title={pending ? `${pending.what} · ${pending.wf.name}` : ""}
        description={
          pending && (
            <span>
              {pending.what === "Disable" ? (
                <>
                  New runs of <b>{pending.wf.name}</b> will be refused for everyone. Runs in flight finish.
                </>
              ) : pending.what === "Require approval" ? (
                <>
                  People will need an approved request before using <b>{pending.wf.name}</b>.
                </>
              ) : (
                <>
                  Apply this change to <b>{pending.wf.name}</b>.
                </>
              )}{" "}
              <Pill tone="neutral">{JSON.stringify(pending.patch)}</Pill>
            </span>
          )
        }
        reasonHint="Logged in the admin action log with your name."
        confirmLabel={pending?.what ?? "Apply"}
        tone={pending?.what === "Disable" ? "danger" : "primary"}
        onConfirm={(reason) => edit.mutateAsync({ ...pending!, reason })}
      />
    </div>
  );
}
