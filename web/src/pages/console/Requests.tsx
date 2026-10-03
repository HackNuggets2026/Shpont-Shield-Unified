import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { admin, type AccessRequest, type CatalogItem, type PrincipalRow, type Workflow } from "../../api";
import { ReasonDialog } from "../../components/Dialog";
import { PersonStatusPill, RequestStatusPill, SensitivityPill, TierPill } from "../../components/pills";
import { Button, Card, Empty, PageHeader, Pill, Q, Segmented, TableWrap, type Tone } from "../../components/ui";
import { IconCheck, IconX } from "../../components/icons";
import { ago, dateTime, minutes } from "../../lib/format";
import { useNow } from "../../lib/useNow";

const KIND: Record<string, { label: string; tone: Tone }> = {
  workflow: { label: "Workflow", tone: "info" },
  quota: { label: "More budget", tone: "warn" },
  grant: { label: "Access grant", tone: "cc" },
};

/** One-line summary of what a request asks for (also used by the employee portal). */
export function requestWhat(r: AccessRequest): string {
  if (r.kind === "workflow") return r.workflow ?? "?";
  if (r.kind === "quota") return `${r.scale ?? 2}× daily budget`;
  const d = r.detail ?? {};
  return `${d.resource ?? "?"}${d.minutes ? ` for ${minutes(d.minutes)}` : ""}${d.actions?.length ? ` (${d.actions.join(", ")})` : ""}`;
}

interface Lookups {
  resources: Record<string, CatalogItem>;
  people: Record<string, PrincipalRow>;
  workflows: Record<string, Workflow>;
}

/** The ask in full: resource title + actions + duration, workflow tier, budget before and after. */
function RequestDetail({ r, lk }: { r: AccessRequest; lk: Lookups }): ReactNode {
  if (r.kind === "grant") {
    const d = r.detail ?? {};
    const res = d.resource ? lk.resources[d.resource] : undefined;
    const actions = d.actions?.length ? d.actions : (res?.actions ?? []);
    return (
      <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-medium text-ink">{res?.title ?? d.resource ?? "?"}</span>
        {actions.length > 0 && <span className="font-mono text-xs text-ink2">{actions.join(", ")}</span>}
        <span className="text-ink2">for {d.minutes ? minutes(d.minutes) : "the maximum"}</span>
        {d.workflow && <span className="text-xs text-muted">only in {d.workflow}</span>}
        {res && <SensitivityPill s={res.sensitivity} />}
        {res?.grant && d.minutes && d.minutes > res.grant.max_minutes && (
          <Pill tone="warn">capped at {minutes(res.grant.max_minutes)}</Pill>
        )}
      </span>
    );
  }
  if (r.kind === "workflow") {
    const w = r.workflow ? lk.workflows[r.workflow] : undefined;
    return (
      <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-mono font-medium text-ink">{r.workflow ?? "?"}</span>
        <span className="text-ink2">workflow</span>
        {w && <TierPill tier={w.tier} />}
        {w && Object.keys(w.resources).length > 0 && (
          <span className="text-xs text-muted">uses {Object.keys(w.resources).map((k) => lk.resources[k]?.title ?? k).join(", ")}</span>
        )}
      </span>
    );
  }
  const p = lk.people[r.principal];
  const scale = r.scale ?? 2;
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className="font-medium text-ink">{scale}× daily budget</span>
      {p && (
        <span className="text-xs text-muted">
          now {Math.round(p.budget_scale * 100)}% → {Math.round(scale * 100)}%
        </span>
      )}
    </span>
  );
}

function approveEffect(r: AccessRequest, lk: Lookups): string {
  if (r.kind === "grant") {
    const d = r.detail ?? {};
    const res = d.resource ? lk.resources[d.resource] : undefined;
    const mins = Math.min(d.minutes ?? res?.grant?.max_minutes ?? 0, res?.grant?.max_minutes ?? Infinity);
    return `${r.principal} gets a live grant on ${res?.title ?? d.resource} that expires ${mins ? `${minutes(mins)} from now` : "at the resource's maximum"}.`;
  }
  if (r.kind === "workflow") return `${r.workflow} appears on ${r.principal}'s menu immediately.`;
  return `${r.principal}'s daily budgets are scaled to ${Math.round((r.scale ?? 2) * 100)}%.`;
}

export function Requests() {
  const qc = useQueryClient();
  const tick = useNow(15_000);
  const now = Math.max(tick, Date.now() / 1000); // renders on every poll too; never show a fresh decision as "in a moment"
  const q = useQuery({ queryKey: ["admin", "requests"], queryFn: () => admin.requests(), refetchInterval: 5_000 });
  const catalog = useQuery({ queryKey: ["admin", "catalog"], queryFn: () => admin.catalog(), refetchInterval: 60_000 });
  const people = useQuery({ queryKey: ["admin", "principals"], queryFn: admin.principals, refetchInterval: 30_000 });
  const menu = useQuery({ queryKey: ["admin", "menu"], queryFn: admin.menu, refetchInterval: 60_000 });
  const [deciding, setDeciding] = useState<{ r: AccessRequest; d: "approve" | "deny" } | null>(null);
  const [done, setDone] = useState<{ r: AccessRequest; d: "approve" | "deny"; at: number } | null>(null);
  const [kind, setKind] = useState<"all" | AccessRequest["kind"]>("all");

  const lk: Lookups = useMemo(() => {
    const resources: Record<string, CatalogItem> = {};
    const c = catalog.data?.classes;
    if (c) for (const r of [...c.consumable, ...c.leasable, ...c.access_grant]) resources[r.name] = r;
    const ppl: Record<string, PrincipalRow> = {};
    for (const p of people.data ?? []) ppl[p.principal] = p;
    const workflows: Record<string, Workflow> = {};
    for (const w of menu.data?.workflows ?? []) workflows[w.name] = w;
    return { resources, people: ppl, workflows };
  }, [catalog.data, people.data, menu.data]);

  return (
    <div className="space-y-4">
      <PageHeader
        title="Requests"
        subtitle="Employees ask for workflows that need approval, more budget, or time-boxed access to sensitive resources."
      />

      {done && (
        <div
          className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg px-4 py-2.5 text-sm ring-1 ring-inset ${
            done.d === "approve" ? "bg-good/10 text-good ring-good/25" : "bg-ink/[0.05] text-ink2 ring-line"
          }`}
          role="status"
        >
          <span className="min-w-0 flex-1">
            {done.d === "approve" ? "Approved. " : "Denied. "}
            {done.d === "approve" ? approveEffect(done.r, lk) : `${done.r.principal} sees your note in the portal.`}
          </span>
          {done.d === "approve" && done.r.kind === "grant" && (
            <Link to="/console/resources" className="font-medium underline underline-offset-2">
              See the live grant
            </Link>
          )}
          {done.d === "approve" && done.r.kind !== "grant" && (
            <Link to={`/console/people/${encodeURIComponent(done.r.principal)}`} className="font-medium underline underline-offset-2">
              Open {done.r.principal}
            </Link>
          )}
          <button type="button" onClick={() => setDone(null)} className="text-xs opacity-70 hover:opacity-100" aria-label="Dismiss">
            <IconX size={14} />
          </button>
        </div>
      )}

      <Q q={q} rows={6}>
        {(all) => {
          const pending = all.filter((r) => r.status === "pending").sort((a, b) => a.ts - b.ts);
          const history = all
            .filter((r) => r.status !== "pending" && (kind === "all" || r.kind === kind))
            .sort((a, b) => (b.decided_at ?? b.ts) - (a.decided_at ?? a.ts));
          return (
            <>
              <Card title={`Waiting for a decision (${pending.length})`} subtitle="Oldest first" flush>
                {pending.length === 0 ? (
                  <Empty title="Inbox zero" hint="New requests from the employee portal land here within a few seconds." icon={<IconCheck size={22} />} />
                ) : (
                  <ul className="divide-y divide-line">
                    {pending.map((r) => {
                      const stale = now - r.ts > 86400;
                      const p = lk.people[r.principal];
                      return (
                        <li key={r.id} className="flex flex-col gap-3 px-4 py-3.5 sm:flex-row sm:items-center">
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <Pill tone={KIND[r.kind]?.tone ?? "neutral"}>{KIND[r.kind]?.label ?? r.kind}</Pill>
                              <Link to={`/console/people/${encodeURIComponent(r.principal)}`} className="font-semibold text-ink hover:text-accent">
                                {r.principal}
                              </Link>
                              {p && (
                                <span className="text-xs text-muted">
                                  {p.team} · {p.role}
                                </span>
                              )}
                              {p && p.status !== "active" && <PersonStatusPill status={p.status} scale={p.budget_scale} />}
                            </div>
                            <div className="mt-1.5 text-sm">
                              <RequestDetail r={r} lk={lk} />
                            </div>
                            <div className="mt-1 break-words text-sm text-ink2">“{r.reason || "no reason given"}”</div>
                            <div className="mt-0.5 text-[11px] text-muted" title={dateTime(r.ts)}>
                              <span className={stale ? "font-medium text-warn" : ""}>asked {ago(r.ts, now)}</span> ·{" "}
                              <span className="font-mono">{r.id}</span>
                            </div>
                          </div>
                          <div className="flex shrink-0 gap-2">
                            <Button variant="good" onClick={() => setDeciding({ r, d: "approve" })}>
                              <IconCheck size={14} /> Approve
                            </Button>
                            <Button onClick={() => setDeciding({ r, d: "deny" })}>
                              <IconX size={14} /> Deny
                            </Button>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </Card>

              <Card
                title="History"
                subtitle="Decided requests, newest first"
                flush
                actions={
                  <Segmented
                    value={kind}
                    onChange={setKind}
                    options={[
                      { value: "all", label: "All" },
                      { value: "grant", label: "Access" },
                      { value: "workflow", label: "Workflow" },
                      { value: "quota", label: "Budget" },
                    ]}
                  />
                }
              >
                {history.length === 0 ? (
                  <Empty title={kind === "all" ? "No decisions yet" : "No decisions of this kind yet"} />
                ) : (
                  <TableWrap>
                    <table className="tbl min-w-[900px]">
                      <thead>
                        <tr>
                          <th>Person</th>
                          <th>Kind</th>
                          <th>For</th>
                          <th>Reason</th>
                          <th>Decision</th>
                          <th>Decided by</th>
                          <th>Note</th>
                          <th>When</th>
                        </tr>
                      </thead>
                      <tbody>
                        {history.map((r) => (
                          <tr key={r.id}>
                            <td className="font-medium">
                              <Link to={`/console/people/${encodeURIComponent(r.principal)}`} className="hover:text-accent">
                                {r.principal}
                              </Link>
                            </td>
                            <td>
                              <Pill tone={KIND[r.kind]?.tone ?? "neutral"}>{KIND[r.kind]?.label ?? r.kind}</Pill>
                            </td>
                            <td className="text-xs">
                              {r.kind === "grant" ? (
                                <>
                                  <div className="text-ink">{lk.resources[r.detail?.resource ?? ""]?.title ?? r.detail?.resource}</div>
                                  {r.detail?.minutes && <div className="text-muted">{minutes(r.detail.minutes)}</div>}
                                </>
                              ) : (
                                <span className="font-mono">{requestWhat(r)}</span>
                              )}
                            </td>
                            <td className="max-w-[220px] text-xs text-ink2">
                              <span className="line-clamp-2" title={r.reason}>
                                {r.reason}
                              </span>
                            </td>
                            <td>
                              <RequestStatusPill status={r.status} />
                            </td>
                            <td className="text-xs">{r.decided_by ?? "—"}</td>
                            <td className="max-w-[200px] text-xs text-ink2">
                              <span className="line-clamp-2" title={r.note}>
                                {r.note || <span className="text-muted">—</span>}
                              </span>
                            </td>
                            <td className="whitespace-nowrap text-xs" title={`asked ${dateTime(r.ts)} · decided ${dateTime(r.decided_at)}`}>
                              <div>{ago(r.decided_at, now)}</div>
                              {r.decided_at && <div className="text-muted">after {minutes((r.decided_at - r.ts) / 60)}</div>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </TableWrap>
                )}
              </Card>
            </>
          );
        }}
      </Q>

      <ReasonDialog
        open={!!deciding}
        onClose={() => setDeciding(null)}
        title={deciding ? `${deciding.d === "approve" ? "Approve" : "Deny"} ${deciding.r.principal}'s request` : ""}
        description={
          deciding && (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <Pill tone={KIND[deciding.r.kind]?.tone ?? "neutral"}>{KIND[deciding.r.kind]?.label ?? deciding.r.kind}</Pill>
                <RequestDetail r={deciding.r} lk={lk} />
              </div>
              <div className="text-muted">“{deciding.r.reason}”</div>
              {deciding.d === "approve" && <div className="rounded-md bg-good/10 px-3 py-2 text-xs text-good">{approveEffect(deciding.r, lk)}</div>}
            </div>
          )
        }
        reasonLabel="Note to the employee"
        reasonHint="Shown to the employee with your name, and kept in the history."
        reasonRequired={deciding?.d === "deny"}
        placeholder={deciding?.d === "deny" ? "Why not, and what to do instead" : "e.g. scoped to the migration check"}
        confirmLabel={deciding?.d === "approve" ? "Approve" : "Deny"}
        tone={deciding?.d === "approve" ? "good" : "danger"}
        onConfirm={async (note) => {
          const cur = deciding!;
          await admin.decide(cur.r.id, cur.d, note);
          setDone({ ...cur, at: Date.now() });
          await qc.invalidateQueries({ queryKey: ["admin"] });
        }}
      />
    </div>
  );
}
