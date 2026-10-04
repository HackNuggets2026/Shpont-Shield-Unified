// One workflow, explained: what it includes, what a run costs, who orders it, what went wrong inside it,
// and the two switches that govern it.
import { useMemo, useState, type ReactNode } from "react";
import { Logo } from "../../components/Logo";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { admin, type Incident, type Workflow } from "../../api";
import { org, orgPath } from "../../orgApi";
import { ReasonDialog } from "../../components/Dialog";
import { BarList, StackedChart } from "../../components/charts";
import { Delta, useDeptColors, useOrg } from "../../components/org";
import { IncidentStatusPill, SeverityPill } from "../../components/pills";
import { Empty, ErrorBox, Loading, Skeleton, Toggle, Wash, cx } from "../../components/ui";
import { IconAlert, IconKey, IconLock, IconServer, IconUsers } from "../../components/icons";
import { Panel, WfBadge, wfPath } from "../../components/wf/bits";
import { CostPerRun } from "../../components/wf/CostPerRun";
import { ago, count, minutes as fmtMinutes, money, pctAuto, titleCase, usd } from "../../lib/format";
import { ruleLabel, SEVERITY_RANK, STATUS_RANK } from "../../lib/security";
import { resourceWord, wfLabel, wfMeta } from "../../lib/workflows";
import { useIncidentTags, useWorkflowBoard, useWorkflowByDeptSeries, useWorkflowTeams, type WfRow } from "../../lib/wfData";

// ---- plain-language scope ------------------------------------------------------------------------

const TOOL_WORDS: [RegExp, string][] = [
  [/^(read_|search_|Read$|Glob$|Grep$|LS$|NotebookRead$)/, "Read and search code"],
  [/^run_tests$/, "Run the test suite"],
  [/^Bash$/, "Run shell commands"],
  [/^(Edit|Write|MultiEdit|NotebookEdit|write_|edit_)/, "Edit files"],
  [/^(Task|TodoWrite)$/, "Plan and hand off sub-tasks"],
  [/^(WebFetch|WebSearch|http_)/, "Fetch web pages"],
  [/^(sql|query|db_)/i, "Query databases"],
  [/^(create_vm|start_vm)/, "Start sandbox VMs"],
  [/^(destroy_vm|stop_vm|delete_vm)/, "Stop sandbox VMs"],
  [/^(boot_|shutdown_|install_app|launch_app)/i, "Drive phone simulators"],
  [/simulator|^sim_/i, "Drive phone simulators"],
  [/^(send_email|email)/i, "Send email"],
];

function toolWords(tools: string[]): string[] {
  const out: string[] = [];
  for (const t of tools) {
    const bare = t.replace(/^\*_?/, "");
    const hit = TOOL_WORDS.find(([re]) => re.test(bare) || re.test(t));
    const w = hit ? hit[1] : titleCase(t.replace(/\*$/, ""));
    if (!out.includes(w)) out.push(w);
  }
  return out;
}

const modelWord = (m: string) => m.replace(/:\*$/, "").replace(/-\*$/, " (any version)").replace(/\*$/, "");

function ScopeRow({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <div className="flex gap-3 py-3 first:pt-0 last:pb-0">
      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-raised text-ink2">{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="text-xs font-medium uppercase tracking-wide text-muted">{title}</div>
        <div className="mt-0.5 text-sm text-ink">{children}</div>
      </div>
    </div>
  );
}

function Includes({ r }: { r: WfRow }) {
  const catalog = useQuery({ queryKey: ["admin", "catalog"], queryFn: () => admin.catalog(), staleTime: 60_000 });
  const grants = useQuery({ queryKey: ["admin", "grants"], queryFn: () => admin.grants(), refetchInterval: 10_000 });
  const wf = r.wf!;
  const sensitive = (catalog.data?.classes.access_grant ?? []).filter((c) => c.workflows.includes(r.name));
  const liveGrants = (grants.data ?? []).filter((g) => g.live && g.workflow === r.name);
  const grantRes = [...new Set(liveGrants.map((g) => g.resource))].filter((x) => !sensitive.some((s) => s.name === x));
  const usedModels = r.resources.filter((x) => !["vm", "simulator", "ci_minutes", "cloud", "(none)"].includes(x.resource)).slice(0, 3);
  const machines = Object.entries(wf.resources);
  return (
    <div className="divide-y divide-line/60">
      <ScopeRow icon={<span className="text-xs font-bold">AI</span>} title="Models">
        {wf.models.length ? `Only ${wf.models.map(modelWord).join(", ")}` : "Any approved model"}
        {usedModels.length > 0 && (
          <div className="mt-0.5 text-xs text-muted">Most spend on {usedModels.map((u) => `${resourceWord(u.resource)} (${money(u.usd)})`).join(", ")}</div>
        )}
      </ScopeRow>
      <ScopeRow icon={<span className="font-mono text-[11px]">{">_"}</span>} title="Tools the agent may use">
        {wf.tools.length ? (
          <ul className="space-y-0.5">
            {toolWords(wf.tools).map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        ) : (
          "No tools: answers only"
        )}
      </ScopeRow>
      <ScopeRow icon={<IconServer size={15} />} title="Machines">
        {machines.length === 0 ? (
          "None. Runs need no simulators or VMs."
        ) : (
          <ul className="space-y-1">
            {machines.map(([res, l]) => {
              const now = r.running.filter((x) => x.resource === res);
              const idle = now.filter((x) => x.flags.length).length;
              return (
                <li key={res}>
                  <span className="font-medium">{resourceWord(res)}</span>
                  <span className="text-ink2">
                    {l.max_concurrent !== null ? `, up to ${l.max_concurrent} per person at a time` : ""}
                    {l.max_minutes !== null ? `, ${fmtMinutes(l.max_minutes)} max per run` : ""}
                  </span>
                  <div className="mt-0.5 flex items-center gap-1.5 text-xs">
                    <span className={cx("h-2 w-2 rounded-full", now.length ? "bg-good" : "bg-ink/20")} />
                    <span className="font-medium text-ink">{now.length} running now</span>
                    {idle > 0 && <span className="text-warn">· {idle} idle too long</span>}
                    <Link to="/console/resources" className="text-accent hover:underline">
                      see
                    </Link>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </ScopeRow>
      <ScopeRow icon={<IconLock size={15} />} title="Sensitive access">
        {sensitive.length === 0 && grantRes.length === 0 ? (
          "None. This workflow can’t touch production data or systems."
        ) : (
          <ul className="space-y-1">
            {sensitive.map((s) => {
              const n = liveGrants.filter((g) => g.resource === s.name).length;
              return (
                <li key={s.name}>
                  <span className="font-medium">{s.title}</span>
                  <span className="text-ink2"> only with a time-boxed grant{s.grant?.approval === "admin" ? " approved by an admin" : ""}</span>
                  <div className="text-xs text-muted">
                    {s.sensitivity} sensitivity · {n ? `${n} live grant${n === 1 ? "" : "s"} now` : "nobody holds it now"}
                  </div>
                </li>
              );
            })}
            {grantRes.map((res) => (
              <li key={res}>
                <span className="font-medium">{resourceWord(res)}</span>
                <span className="text-ink2"> · {liveGrants.filter((g) => g.resource === res).length} live grant(s) scoped to this workflow</span>
              </li>
            ))}
          </ul>
        )}
      </ScopeRow>
      <ScopeRow icon={<IconUsers size={15} />} title="Who may use it">
        {wf.teams.length || wf.roles.length ? [...wf.teams.map((t) => `team ${t}`), ...wf.roles.map((x) => `${x}s`)].join(", ") : "Everyone in the company"}
        {wf.approval === "admin" && <div className="text-xs text-cc">after an admin approves their request</div>}
      </ScopeRow>
    </div>
  );
}

// ---- incidents and interventions ---------------------------------------------------------------------

function Interventions({ r }: { r: WfRow }) {
  const parts = [
    { k: "Blocked", v: r.blocked, cls: "bg-bad" },
    { k: "Redacted", v: r.redacted, cls: "bg-serious" },
    { k: "Warned", v: r.warned, cls: "bg-warn" },
  ];
  const n = parts.reduce((a, p) => a + p.v, 0);
  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="text-2xl font-semibold tracking-tight text-ink">{pctAuto(r.adherence)}</span>
        <span className="text-sm text-ink2">of {count(r.checks)} checks needed nothing</span>
      </div>
      <p className="mt-1 text-xs text-muted">The other {count(n)} were stopped or cleaned up by policy before reaching a model or tool:</p>
      <div className="mt-3 flex h-2.5 gap-[2px] overflow-hidden rounded-full bg-ink/[0.07]">
        {parts.map((p) => (p.v ? <span key={p.k} className={cx("h-full", p.cls)} style={{ width: `${(p.v / (n || 1)) * 100}%` }} title={`${p.k}: ${count(p.v)}`} /> : null))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {parts.map((p) => (
          <span key={p.k} className="inline-flex items-center gap-1.5 text-ink2">
            <span className={cx("h-2 w-2 rounded-sm", p.cls)} />
            {p.k} <b className="tnum font-semibold text-ink">{count(p.v)}</b>
          </span>
        ))}
      </div>
    </div>
  );
}

const sevFirst = (a: Incident, b: Incident) =>
  (a.status === "open" || a.status === "acknowledged" ? 0 : 1) - (b.status === "open" || b.status === "acknowledged" ? 0 : 1) ||
  (SEVERITY_RANK[a.severity] ?? 9) - (SEVERITY_RANK[b.severity] ?? 9) ||
  (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9) ||
  b.ts - a.ts;

function WfIncidents({ name }: { name: string }) {
  const inc = useQuery({ queryKey: ["admin", "incidents", "list"], queryFn: () => admin.incidents(), refetchInterval: 15_000 });
  const tags = useIncidentTags();
  const nav = useNavigate();
  if (inc.isPending || tags.isPending) return <Loading rows={4} />;
  if (inc.isError) return <ErrorBox error={inc.error} retry={() => inc.refetch()} />;
  const mine = inc.data.incidents.filter((i) => tags.data?.[i.id]?.workflow === name).sort(sevFirst);
  if (!mine.length) return <Empty title="No incidents in this workflow" hint="Detections tagged to this kind of work show up here, high severity first." />;
  const active = mine.filter((i) => i.status === "open" || i.status === "acknowledged").length;
  return (
    <div>
      <div className="mb-2 text-xs text-muted">
        {active} need attention · {mine.length - active} closed in the last 30 days
      </div>
      <ul className="-mx-2 space-y-1">
        {mine.slice(0, 7).map((i) => {
          const t = tags.data?.[i.id];
          return (
            <li key={i.id}>
              <button
                type="button"
                onClick={() => nav(`/console/incidents/${encodeURIComponent(i.id)}`)}
                className={cx("flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left hover:bg-raised/70", i.status === "open" && i.severity === "high" && "bg-bad/[0.05]")}
              >
                <SeverityPill severity={i.severity} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-ink">
                    {ruleLabel(i.rule)} <span className="font-normal text-ink2">· {i.principal}</span>
                  </div>
                  <div className="truncate text-xs text-muted">
                    {i.detail}
                    {t?.likely ? " · workflow inferred" : ""}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <IncidentStatusPill status={i.status} />
                  <div className="mt-0.5 text-[11px] text-muted">{ago(i.ts)}</div>
                </div>
              </button>
            </li>
          );
        })}
      </ul>
      {mine.length > 7 && (
        <Link to="/console/security" className="mt-2 inline-block text-xs font-medium text-accent hover:underline">
          All {mine.length} in Security →
        </Link>
      )}
    </div>
  );
}

// ---- settings ----------------------------------------------------------------------------------------

interface Pending {
  wf: Workflow;
  patch: Record<string, unknown>;
  what: string;
}

function Settings({ wf }: { wf: Workflow }) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<Pending | null>(null);
  const edit = useMutation({
    mutationFn: ({ wf, patch, reason }: Pending & { reason: string }) => admin.editWorkflow(wf.name, patch, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "menu"] }),
  });
  const caps = [wf.per_run.usd !== null ? `${usd(wf.per_run.usd)} per run` : null, wf.per_run.tokens !== null ? `${count(wf.per_run.tokens)} tokens per run` : null].filter(Boolean);
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3 rounded-xl bg-raised/60 px-3 py-2.5">
        <div className="min-w-0">
          <div className="text-sm font-medium text-ink">On the menu</div>
          <div className="text-xs text-muted">{wf.enabled ? "People can start runs" : "New runs are refused"}</div>
        </div>
        <Toggle label={`${wf.name} enabled`} checked={wf.enabled} onChange={(v) => setPending({ wf, patch: { enabled: v }, what: v ? "Enable" : "Disable" })} />
      </div>
      <div className="flex items-center justify-between gap-3 rounded-xl bg-raised/60 px-3 py-2.5">
        <div className="min-w-0">
          <div className="text-sm font-medium text-ink">Needs approval</div>
          <div className="text-xs text-muted">{wf.approval === "admin" ? "An admin approves each person first" : "Anyone allowed can start right away"}</div>
        </div>
        <Toggle
          label={`${wf.name} requires admin approval`}
          checked={wf.approval === "admin"}
          onChange={(v) => setPending({ wf, patch: { approval: v ? "admin" : "none" }, what: v ? "Require approval" : "Remove approval" })}
        />
      </div>
      <p className="text-xs text-muted">
        Spending cap: {caps.length ? caps.join(" · ") : "none set"}. Changes go to the policy overlay, are validated and apply within seconds.
      </p>
      <ReasonDialog
        open={!!pending}
        onClose={() => setPending(null)}
        title={pending ? `${pending.what} · ${wfLabel(pending.wf.name)}` : ""}
        description={
          pending &&
          (pending.what === "Disable"
            ? "New runs will be refused for everyone. Runs in flight finish."
            : pending.what === "Require approval"
              ? "People will need an approved request before using this workflow."
              : "Apply this change to the workflow.")
        }
        reasonHint="Logged in the admin action log with your name."
        confirmLabel={pending?.what ?? "Apply"}
        tone={pending?.what === "Disable" ? "danger" : "primary"}
        onConfirm={(reason) => edit.mutateAsync({ ...pending!, reason })}
      />
    </div>
  );
}

// ---- page --------------------------------------------------------------------------------------------

export function WorkflowPage() {
  const { name = "" } = useParams();
  const b = useWorkflowBoard();
  const deptColors = useDeptColors();
  const o = useOrg(30);
  const departments = useMemo(() => (o.data?.departments ?? []).map((d) => d.name), [o.data]);
  const series = useWorkflowByDeptSeries(name, departments);
  const teams = useWorkflowTeams();
  const teamDirectory = useQuery({ queryKey: ["admin", "org", "teams", "all-names"], queryFn: () => org.teams({ limit: 200, sort: "usd" }), staleTime: 60_000 });
  const r = b.rows.find((x) => x.name === name);

  const topTeams = useMemo(() => {
    const dept = Object.fromEntries((teamDirectory.data?.rows ?? []).map((t) => [t.name, t.department ?? ""]));
    return (teams.data ?? [])
      .filter((t) => String(t.workflow) === name && t.usd > 0)
      .sort((x, y) => y.usd - x.usd)
      .slice(0, 8)
      .map((t) => ({ team: String(t.team), department: dept[String(t.team)] ?? "", usd: t.usd, requests: t.requests }));
  }, [teams.data, teamDirectory.data, name]);

  if (b.isPending)
    return (
      <div className="space-y-4">
        <Skeleton className="h-16 w-96" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-72 w-full" />
      </div>
    );
  if (b.error) return <ErrorBox error={b.error} retry={b.refetch} />;
  if (!r || !r.wf)
    return <Empty title={`No workflow called “${name}” on the menu`} hint={<Link to="/console/workflows" className="text-accent hover:underline">Back to workflows</Link>} />;

  const meta = wfMeta(r.name, r.wf.description);
  const total = b.rows.reduce((a, x) => a + x.usd, 0);
  const machines = r.running.length;
  const teamMax = Math.max(...topTeams.map((t) => t.usd), 1);

  return (
    <div className="space-y-6">
      <div>
        <div className="mb-3 text-xs text-muted">
          <Link to="/console/workflows" className="hover:text-accent">
            Workflows
          </Link>{" "}
          / {wfLabel(r.name)}
        </div>
        <div className="soft-card relative flex flex-wrap items-start justify-between gap-4 overflow-hidden rounded-2xl p-5">
          <Wash color={r.color} height="100%" opacity={0.12} />
          <div className="relative flex min-w-0 items-start gap-4">
            <WfBadge id={r.name} color={r.color} size="lg" />
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-2xl font-semibold tracking-tight text-ink">{wfLabel(r.name)}</h1>
                <span className="font-mono text-xs text-muted">{r.name}</span>
                {r.wf.approval === "admin" && <span className="rounded-full bg-cc/10 px-2 py-0.5 text-[11px] font-medium text-cc">needs approval</span>}
                {!r.wf.enabled && <span className="rounded-full bg-ink/10 px-2 py-0.5 text-[11px] font-medium text-ink2">switched off</span>}
              </div>
              <p className="mt-1 max-w-2xl text-sm text-ink2">{meta.pitch}</p>
              {meta.example && <p className="mt-0.5 text-xs text-muted">For example: {meta.example}</p>}
            </div>
          </div>
          <div className="relative flex max-w-md flex-wrap justify-end gap-1.5">
            {b.menuRows
              .filter((x) => x.name !== r.name)
              .map((x) => (
                <Link key={x.name} to={wfPath(x.name)} className="inline-flex items-center gap-1.5 rounded-full bg-panel px-2.5 py-1 text-xs font-medium text-ink2 ring-1 ring-line hover:bg-raised hover:text-ink">
                  <span className="h-2 w-2 rounded-full" style={{ background: x.color }} />
                  {wfLabel(x.name)}
                </Link>
              ))}
          </div>
        </div>
      </div>

      <p className="tnum -mt-3 flex flex-wrap items-center gap-x-1.5 text-xs text-muted">
        <span className="font-medium text-ink2">{money(r.usd)}</span> in 30 days
        <Delta cur={r.trend.recent} prev={r.trend.earlier} className="text-[11px]" title="Last 14 days vs the 14 before" />
        <span>· {pctAuto(r.usd / (total || 1))} of all AI spend · {count(r.runs)} runs · {pctAuto(r.adherence)} adherence, {count(r.blocked)} blocked</span>
        {Object.keys(r.wf.resources).length > 0 && <span>· {machines} running now</span>}
      </p>

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="min-w-0 space-y-5 lg:col-span-2">
          <Panel title="Spend over time, by department" hint="Daily cost of this workflow, last 30 days">
            {series.isPending ? (
              <Loading rows={6} />
            ) : series.isError ? (
              <ErrorBox error={series.error} retry={() => series.refetch()} />
            ) : (
              <StackedChart ts={series.data} kind="bar" metric="usd" colors={deptColors} compact height={240} />
            )}
          </Panel>

          <Panel title="What one run costs" hint="From every measured run in the last 30 days">
            <CostPerRun p50={r.p50} p90={r.p90} mean={r.mean} cap={r.wf.per_run.usd} runs={r.runs} color={r.color} />
          </Panel>

          <div className="grid gap-5 md:grid-cols-2">
            <Panel title="Policy at work" hint="Checks on prompts, outputs and tool calls in this workflow">
              <Interventions r={r} />
            </Panel>
            <Panel title="Top teams" hint="Who runs the most of it" flush>
              {teams.isPending ? (
                <div className="px-5 pb-5">
                  <Loading rows={5} />
                </div>
              ) : topTeams.length === 0 ? (
                <Empty title="No team has run it yet" />
              ) : (
                <ul className="px-5 pb-4">
                  {topTeams.map((t) => (
                    <li key={t.team} className="py-1.5">
                      <Link to={orgPath.team(t.team)} className="group block">
                        <div className="flex items-baseline justify-between gap-2 text-sm">
                          <span className="min-w-0 truncate">
                            <span className="font-medium text-ink group-hover:text-accent">{t.team}</span>
                            <span className="ml-1.5 text-xs text-muted">{t.department}</span>
                          </span>
                          <span className="tnum shrink-0 font-medium text-ink">{money(t.usd)}</span>
                        </div>
                        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-ink/[0.07]">
                          <div className="h-full rounded-full" style={{ width: `${(t.usd / teamMax) * 100}%`, background: deptColors[t.department] ?? "var(--s-other)" }} />
                        </div>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>

          <Panel
            title={
              <span className="inline-flex items-center gap-2">
                <IconAlert size={15} className="text-bad" /> Incidents in this workflow
              </span>
            }
            hint="High severity first. Tagged from the evidence, or inferred from the person’s usual work."
          >
            <WfIncidents name={r.name} />
          </Panel>
        </div>

        <div className="min-w-0 space-y-5">
          <Panel title="What it includes" hint="In plain words, from the policy">
            <Includes r={r} />
          </Panel>
          <Panel title="Where its money goes" hint="Last 30 days, by resource">
            <BarList
              rows={r.resources.slice(0, 6).map((x) => ({
                key: x.resource,
                label: resourceWord(x.resource),
                icon: <Logo id={x.resource} label={resourceWord(x.resource)} size={14} />,
                value: x.usd,
                color: r.color,
                sub: x.minutes ? `${count(Math.round(x.minutes / 60))} machine-hours` : undefined,
              }))}
              fmt={money}
            />
          </Panel>
          <Panel
            title={
              <span className="inline-flex items-center gap-2">
                <IconKey size={15} /> Settings
              </span>
            }
          >
            <Settings wf={r.wf} />
          </Panel>
        </div>
      </div>
    </div>
  );
}
