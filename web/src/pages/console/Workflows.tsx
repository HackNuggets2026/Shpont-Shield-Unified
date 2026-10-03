import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { admin, type Workflow } from "../../api";
import { ReasonDialog } from "../../components/Dialog";
import { TierPill } from "../../components/pills";
import { Card, Empty, ErrorBox, PageHeader, Pill, Q, Stat, TableWrap, Toggle } from "../../components/ui";
import { count, money, share, tokens, unitMoney, usd } from "../../lib/format";
import { org } from "../../orgApi";
import { useDeptColors } from "../../components/org";
import { DeptLegend, WfBadge, wfPath } from "../../components/wf/bits";
import { TileSkeleton, WorkflowTile } from "../../components/wf/MenuTiles";
import { useIncidentTags, useWorkflowBoard } from "../../lib/wfData";
import { useWorkflowColors, wfLabel } from "../../lib/workflows";

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

/** A 100% bar of a workflow's spend: its top three departments, the rest folded into gray; top three named underneath. */
function DeptSplit({ rows: all, colors }: { rows: { department: string; usd: number }[]; colors: Record<string, string> }) {
  const total = all.reduce((a, r) => a + r.usd, 0);
  if (!total) return <span className="text-xs text-muted">—</span>;
  const rest = all.slice(3).reduce((a, r) => a + r.usd, 0);
  const rows = rest > 0 ? [...all.slice(0, 3), { department: "Other", usd: rest }] : all.slice(0, 3);
  return (
    <div title={all.map((r) => `${r.department}: ${money(r.usd)} (${share(r.usd / total)})`).join("\n")}>
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-ink/[0.07]">
        {rows.map((r) => (
          <span key={r.department} className="h-full border-r border-panel last:border-r-0" style={{ width: `${(r.usd / total) * 100}%`, background: colors[r.department] ?? "var(--s-other)" }}
            title={`${r.department}: ${money(r.usd)}`} />
        ))}
      </div>
      <div className="mt-1 truncate text-[11px] text-muted">
        {rows
          .filter((r) => r.department !== "Other")
          .map((r) => `${r.department} ${share(r.usd / total)}`)
          .join(" · ")}
      </div>
    </div>
  );
}

/** One card per workflow, biggest spend first, with the work done outside the menu underneath. */
function MenuGrid() {
  const b = useWorkflowBoard();
  const deptColors = useDeptColors();
  const open = useQuery({ queryKey: ["admin", "incidents", "open"], queryFn: () => admin.incidents("open"), refetchInterval: 10_000 });
  const tags = useIncidentTags();
  const openByWf = useMemo(() => {
    const m: Record<string, number> = {};
    for (const i of open.data?.incidents ?? []) {
      const w = tags.data?.[i.id]?.workflow;
      if (w) m[w] = (m[w] ?? 0) + 1;
    }
    return m;
  }, [open.data, tags.data]);
  const total = b.rows.reduce((a, r) => a + r.usd, 0);
  const off = b.offMenu.reduce((a, r) => a + r.usd, 0);
  if (b.error) return <ErrorBox error={b.error} retry={b.refetch} />;
  return (
    <section className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {b.isPending
          ? Array.from({ length: 6 }, (_, i) => <TileSkeleton key={i} />)
          : b.menuRows.map((r) => <WorkflowTile key={r.name} r={r} deptColors={deptColors} openIncidents={openByWf[r.name]} />)}
      </div>
      {off > 0 && (
        <div className="rounded-2xl border border-dashed border-line bg-panel/60 px-5 py-4 text-sm">
          <span className="font-semibold text-ink">{money(off)} isn’t on the menu</span>
          <span className="text-ink2"> · {share(off / (total || 1))} of spend</span>
          <p className="mt-0.5 text-xs text-muted">
            {[...b.offMenu]
              .sort((x, y) => y.usd - x.usd)
              .map((r) => `${r.name === "unlabeled" ? "Unlabeled requests (auto-classified when possible)" : "Not attributed to any workflow (imported cloud bill, untagged CI)"}: ${money(r.usd)}`)
              .join(" · ")}
          </p>
        </div>
      )}
    </section>
  );
}

export function Workflows() {
  const qc = useQueryClient();
  const menu = useQuery({ queryKey: ["admin", "menu"], queryFn: admin.menu, refetchInterval: 30_000 });
  const spend = useQuery({ queryKey: ["admin", "usage", "workflow", 30], queryFn: () => org.usage("workflow", 30) });
  const byDept = useQuery({ queryKey: ["admin", "usage", "workflow,department", 30], queryFn: () => org.usage("workflow,department", 30), retry: 1 });
  const value = useQuery({ queryKey: ["admin", "value", "workflow", 30], queryFn: () => org.value("workflow", 30) });
  const colors = useDeptColors();
  const wfColors = useWorkflowColors();
  const navigate = useNavigate();
  const [pending, setPending] = useState<Pending | null>(null);
  const edit = useMutation({
    mutationFn: ({ wf, patch, reason }: Pending & { reason: string }) => admin.editWorkflow(wf.name, patch, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "menu"] }),
  });

  const spend30 = useMemo(() => Object.fromEntries((spend.data ?? []).map((r) => [String(r.workflow ?? "(none)"), r.usd])), [spend.data]);
  const output = useMemo(() => Object.fromEntries((value.data?.rows ?? []).map((r) => [r.key, r])), [value.data]);
  const depts = useMemo(() => {
    const out: Record<string, { department: string; usd: number }[]> = {};
    for (const r of byDept.data ?? []) (out[String(r.workflow ?? "(none)")] ??= []).push({ department: String(r.department ?? "(none)"), usd: r.usd });
    for (const k of Object.keys(out)) out[k].sort((a, b) => b.usd - a.usd);
    return out;
  }, [byDept.data]);
  const totalSpend = Object.values(spend30).reduce((a, b) => a + b, 0);
  const totalRuns = (menu.data?.workflows ?? []).reduce((a, w) => a + w.measured.runs, 0);
  const totalCommits = (value.data?.rows ?? []).reduce((a, r) => a + r.commits, 0);
  const totalCc = (value.data?.rows ?? []).reduce((a, r) => a + r.claude_code_usd, 0);
  // Commits Claude Code reports without a workflow label cannot be priced per workflow; below half coverage a
  // per-workflow $/commit would divide a whole workflow's spend by a sliver of its commits.
  const labelledCommits = (value.data?.rows ?? []).filter((r) => r.key !== "(none)" && r.key !== "unlabeled").reduce((a, r) => a + r.commits, 0);
  const commitCoverage = totalCommits ? labelledCommits / totalCommits : 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Workflows"
        subtitle="The priced menu of AI work: what each kind of task may use, what one run really costs, and who orders it. Last 30 days; open a card for the details."
      />
      <Q q={menu} rows={8}>
        {(m) => (
          <div className="space-y-6">
            <div className="soft-card grid grid-cols-2 gap-4 rounded-2xl px-5 py-4 sm:grid-cols-3 xl:grid-cols-6">
              <Stat label="Workflows" value={`${m.workflows.filter((w) => w.enabled).length} enabled / ${m.workflows.length}`} />
              <Stat label="Need approval" value={m.workflows.filter((w) => w.approval !== "none").length} />
              <Stat label="Runs, 30 days" value={count(totalRuns)} />
              <Stat label="Spend, 30 days" value={money(totalSpend)} />
              <Stat label="Claude Code $ / commit" value={totalCommits ? `${unitMoney(totalCc / totalCommits)} · ${count(totalCommits)} commits` : "—"} />
              <Stat
                label="Labels"
                value={`${m.require_label ? "required" : "optional"} · ${m.classify_unlabeled ? "unlabeled auto-classified" : "unlabeled kept"}`}
              />
            </div>

            <MenuGrid />

            <Card
              flush
              title="Compare and switch"
              subtitle={`Cost per run p50 – p90 · top departments by spend · approval and on/off for each workflow${totalCommits && commitCoverage < 0.5 ? ` · ${share(1 - commitCoverage)} of Claude Code commits carry no workflow label` : ""}`}
              actions={<DeptLegend colors={colors} />}
            >
              {m.workflows.length === 0 ? (
                <Empty title="No workflows on the menu" hint="Add workflows under menu.workflows in policy.yaml." />
              ) : (
                <TableWrap>
                  <table className="tbl min-w-[920px]">
                    <thead>
                      <tr>
                        <th>Workflow</th>
                        <th className="text-right">Cost / run</th>
                        <th className="text-right">Runs</th>
                        <th className="text-right">Spend 30d</th>
                        <th title="Share of the workflow's spend by its top three departments">Top departments</th>
                        <th className="text-right" title="Claude Code spend in the workflow over 30 days, divided by the commits Claude Code reported in it">
                          CC $ / commit
                        </th>
                        <th className="text-center">Approval</th>
                        <th className="text-center">Enabled</th>
                      </tr>
                    </thead>
                    <tbody>
                      {m.workflows.map((w) => (
                        <tr key={w.name} className={w.enabled ? "row-link" : "row-link opacity-60"} onClick={() => navigate(wfPath(w.name))}>
                          <td className="max-w-[260px]">
                            <Link to={wfPath(w.name)} className="flex items-center gap-2.5" onClick={(e) => e.stopPropagation()}>
                              <WfBadge id={w.name} color={wfColors[w.name] ?? "var(--s-other)"} size="sm" />
                              <span className="min-w-0">
                                <span className="flex items-center gap-1.5">
                                  <span className="truncate font-medium text-ink hover:text-accent">{wfLabel(w.name)}</span>
                                  <TierPill tier={w.tier} />
                                </span>
                                <span className="block truncate text-xs text-muted" title={limits(w).join(" · ")}>
                                  {limits(w).join(" · ") || "no limits"}
                                </span>
                              </span>
                            </Link>
                          </td>
                          <td className="text-right">{costRange(w.measured)}</td>
                          <td className="tnum text-right">{count(w.measured.runs)}</td>
                          <td className="tnum whitespace-nowrap text-right">
                            <div className="font-medium text-ink">{money(spend30[w.name] ?? 0)}</div>
                            <div className="text-[11px] text-muted">{share(totalSpend ? (spend30[w.name] ?? 0) / totalSpend : 0)} of all</div>
                          </td>
                          <td className="min-w-[170px] max-w-[260px]">
                            <DeptSplit rows={depts[w.name] ?? []} colors={colors} />
                          </td>
                          <td className="tnum text-right">
                            {(() => {
                              const o = output[w.name];
                              if (!o?.commits) return <span className="text-muted">—</span>;
                              if (commitCoverage < 0.5)
                                return (
                                  <span className="text-muted" title={`Only ${share(commitCoverage)} of commits carry a workflow label; see the org-wide figure above.`}>
                                    {count(o.commits)} labelled
                                  </span>
                                );
                              return (
                                <span title={`${count(o.commits)} commits · ${count(o.pull_requests)} PRs · ${count(o.lines_added)} lines added`}>
                                  {unitMoney(o.claude_code_usd / o.commits)}
                                  <div className="text-[11px] text-muted">{count(o.commits)} commits</div>
                                </span>
                              );
                            })()}
                          </td>
                          <td onClick={(e) => e.stopPropagation()}>
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
                          <td className="text-center" onClick={(e) => e.stopPropagation()}>
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
