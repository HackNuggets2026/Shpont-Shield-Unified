import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { admin, type AccessRequest, type CatalogItem, type PrincipalRow, type Workflow } from "../../api";
import { ReasonDialog } from "../../components/Dialog";
import { PersonStatusPill, RequestStatusPill, SensitivityPill, TierPill } from "../../components/pills";
import { Button, Card, Empty, PageHeader, Pill, Q, Segmented, TableWrap, cx, type Tone } from "../../components/ui";
import { IconCheck, IconX } from "../../components/icons";
import { DepartmentSelect, OrgLine, Pager, useUrlFilters } from "../../components/opsKit";
import { displayName, ops, type OrgRequest } from "../../opsApi";
import { age, countC } from "../../lib/compact";
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

function approveEffect(r: OrgRequest, lk: Lookups): string {
  const who = displayName(r);
  if (r.kind === "grant") {
    const d = r.detail ?? {};
    const res = d.resource ? lk.resources[d.resource] : undefined;
    const mins = Math.min(d.minutes ?? res?.grant?.max_minutes ?? 0, res?.grant?.max_minutes ?? Infinity);
    return `${who} gets a live grant on ${res?.title ?? d.resource} that expires ${mins ? `${minutes(mins)} from now` : "at the resource's maximum"}.`;
  }
  if (r.kind === "workflow") return `${r.workflow} appears on ${who}'s menu immediately.`;
  return `${who}'s daily budgets are scaled to ${Math.round((r.scale ?? 2) * 100)}%.`;
}

const PENDING_PAGE = 50;
const HISTORY_PAGE = 25;
const STALE = 86400;
type Decision = "approve" | "deny";
type KindFilter = "" | AccessRequest["kind"];
const KINDS: { value: KindFilter; label: string }[] = [
  { value: "", label: "All" },
  { value: "grant", label: "Access" },
  { value: "workflow", label: "Workflow" },
  { value: "quota", label: "Budget" },
];

function Person({ r }: { r: OrgRequest }) {
  return (
    <div className="min-w-0">
      <Link to={`/console/people/${encodeURIComponent(r.principal)}`} onClick={(e) => e.stopPropagation()} className="font-medium text-ink hover:text-accent">
        {displayName(r)}
      </Link>
      <OrgLine team={r.team} department={r.department} />
    </div>
  );
}

interface BulkResult {
  d: Decision;
  ok: OrgRequest[];
  failed: { r: OrgRequest; error: string }[];
}

export function Requests() {
  const qc = useQueryClient();
  const tick = useNow(15_000);
  const now = Math.max(tick, Date.now() / 1000); // renders on every poll too; never show a fresh decision as "in a moment"
  const f = useUrlFilters(["kind", "department"] as const);
  const h = useUrlFilters(["status"] as const, {}, "h_");
  const kind = f.values.kind as KindFilter;
  const department = f.values.department;
  const hStatus = h.values.status as "" | "approved" | "denied";

  const overview = useQuery({ queryKey: ["admin", "overview"], queryFn: admin.overview, refetchInterval: 5_000 });
  const pending = useQuery({
    queryKey: ["admin", "requests", "pending", kind, department, f.page],
    queryFn: () => ops.requests({ status: "pending", kind, department, limit: PENDING_PAGE, offset: f.page * PENDING_PAGE }),
    placeholderData: keepPreviousData,
    refetchInterval: 5_000,
  });
  const history = useQuery({
    queryKey: ["admin", "requests", "history", kind, department, hStatus, h.page],
    queryFn: async () => {
      if (hStatus) return ops.requests({ status: hStatus, kind, department, limit: HISTORY_PAGE, offset: h.page * HISTORY_PAGE });
      // No "approved or denied" status on the server: read from the top past the pending rows mixed in, then page here.
      const extra = (await ops.requests({ status: "pending", kind, department, limit: 1000 })).rows.length;
      const want = (h.page + 1) * HISTORY_PAGE;
      const r = await ops.requests({ kind, department, limit: want + extra });
      const all = r.rows.filter((x) => x.status !== "pending");
      return { rows: all.slice(h.page * HISTORY_PAGE, want), total: r.hasMore ? null : all.length, hasMore: r.hasMore || all.length > want };
    },
    placeholderData: keepPreviousData,
    refetchInterval: 10_000,
  });
  const catalog = useQuery({ queryKey: ["admin", "catalog"], queryFn: () => admin.catalog(), refetchInterval: 60_000 });
  const people = useQuery({ queryKey: ["admin", "principals"], queryFn: admin.principals, refetchInterval: 30_000 });
  const menu = useQuery({ queryKey: ["admin", "menu"], queryFn: admin.menu, refetchInterval: 60_000 });

  const [deciding, setDeciding] = useState<{ r: OrgRequest; d: Decision } | null>(null);
  const [bulk, setBulk] = useState<Decision | null>(null);
  const [done, setDone] = useState<{ r: OrgRequest; d: Decision } | null>(null);
  const [bulkDone, setBulkDone] = useState<BulkResult | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [progress, setProgress] = useState<{ n: number; of: number } | null>(null);

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

  // Older servers ignore kind/department: narrow the page here too, so the queue never shows the wrong rows.
  const match = (r: OrgRequest) => (!kind || r.kind === kind) && (!department || !r.department || r.department === department);
  const queue = useMemo(() => (pending.data?.rows ?? []).filter((r) => r.status === "pending" && match(r)).sort((a, b) => a.ts - b.ts), [pending.data, kind, department]); // eslint-disable-line react-hooks/exhaustive-deps
  const decided = (history.data?.rows ?? []).filter((r) => r.status !== "pending" && match(r)).sort((a, b) => (b.decided_at ?? b.ts) - (a.decided_at ?? a.ts));
  const pendingTotal = pending.data?.total ?? (!kind && !department ? (overview.data?.requests_pending ?? null) : null);
  const staleCount = queue.filter((r) => now - r.ts > STALE).length;

  // Drop selections that left the queue (decided elsewhere, filtered out).
  useEffect(() => {
    setSelected((s) => {
      const ids = new Set(queue.map((r) => r.id));
      const n = new Set([...s].filter((id) => ids.has(id)));
      return n.size === s.size ? s : n;
    });
  }, [queue]);

  const chosen = queue.filter((r) => selected.has(r.id));
  const allOnPage = queue.length > 0 && chosen.length === queue.length;
  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const runBulk = async (d: Decision, note: string) => {
    const rows = chosen;
    const res: BulkResult = { d, ok: [], failed: [] };
    setProgress({ n: 0, of: rows.length });
    // One at a time: approvals write policy overlays, and the server serialises them anyway.
    for (const [i, r] of rows.entries()) {
      try {
        await admin.decide(r.id, d, note);
        res.ok.push(r);
      } catch (e) {
        res.failed.push({ r, error: e instanceof Error ? e.message : String(e) });
      }
      setProgress({ n: i + 1, of: rows.length });
    }
    setProgress(null);
    setDone(null);
    setBulkDone(res);
    setSelected(new Set(res.failed.map((x) => x.r.id)));
    await qc.invalidateQueries({ queryKey: ["admin"] });
    if (!res.ok.length) throw new Error(res.failed[0]?.error ?? "Nothing was decided");
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Requests"
        subtitle="Employees ask for workflows that need approval, more budget, or time-boxed access to sensitive resources."
      />

      <div className="flex flex-wrap items-center gap-2">
        <Segmented<KindFilter> value={kind} onChange={(v) => f.set({ kind: v })} options={KINDS} />
        <DepartmentSelect value={department} onChange={(v) => f.set({ department: v })} />
        {f.dirty && (
          <button type="button" onClick={f.reset} className="text-xs font-medium text-accent hover:underline">
            Reset
          </button>
        )}
      </div>

      {done && (
        <Banner tone={done.d === "approve" ? "good" : "neutral"} onClose={() => setDone(null)}>
          <span className="min-w-0 flex-1">
            {done.d === "approve" ? "Approved. " : "Denied. "}
            {done.d === "approve" ? approveEffect(done.r, lk) : `${displayName(done.r)} sees your note in the portal.`}
          </span>
          {done.d === "approve" && done.r.kind === "grant" && (
            <Link to="/console/resources?tab=access" className="font-medium underline underline-offset-2">
              See the live grant
            </Link>
          )}
          {done.d === "approve" && done.r.kind !== "grant" && (
            <Link to={`/console/people/${encodeURIComponent(done.r.principal)}`} className="font-medium underline underline-offset-2">
              Open {displayName(done.r)}
            </Link>
          )}
        </Banner>
      )}
      {bulkDone && (
        <Banner tone={bulkDone.failed.length ? "warn" : bulkDone.d === "approve" ? "good" : "neutral"} onClose={() => setBulkDone(null)}>
          <span className="min-w-0 flex-1">
            {bulkDone.d === "approve" ? "Approved" : "Denied"} {bulkDone.ok.length} request{bulkDone.ok.length === 1 ? "" : "s"}
            {bulkDone.ok.length > 0 && bulkDone.ok.length <= 4 ? ` (${bulkDone.ok.map(displayName).join(", ")})` : ""}.
            {bulkDone.failed.length > 0 && (
              <>
                {" "}
                {bulkDone.failed.length} failed and stay selected: {bulkDone.failed.slice(0, 3).map((x) => `${displayName(x.r)}: ${x.error}`).join("; ")}
                {bulkDone.failed.length > 3 ? "…" : ""}
              </>
            )}
          </span>
        </Banner>
      )}

      <Card
        title={
          <span>
            Waiting for a decision{" "}
            <span className="tnum font-normal text-muted">
              · {pendingTotal !== null ? countC(pendingTotal) : `${queue.length}${pending.data?.hasMore ? "+" : ""}`}
              {staleCount > 0 && <span className="text-warn"> · {countC(staleCount)} over a day</span>} · oldest first
            </span>
          </span>
        }
        flush
      >
        <div className={cx("flex flex-wrap items-center gap-2 border-b border-line px-4 py-2 text-xs", chosen.length ? "bg-accent/[0.06]" : "")}>
          <label className="inline-flex items-center gap-2 text-ink2">
            <input
              type="checkbox"
              className="h-4 w-4 accent-[rgb(var(--accent))]"
              checked={allOnPage}
              ref={(el) => {
                if (el) el.indeterminate = chosen.length > 0 && !allOnPage;
              }}
              onChange={() => setSelected(allOnPage ? new Set() : new Set(queue.map((r) => r.id)))}
              disabled={!queue.length}
              aria-label="Select every request on this page"
            />
            {chosen.length ? <b className="text-ink">{chosen.length} selected</b> : "Select all"}
          </label>
          {chosen.length > 0 && (
            <>
              <Button size="sm" variant="good" onClick={() => setBulk("approve")}>
                <IconCheck size={13} /> Approve {chosen.length}
              </Button>
              <Button size="sm" onClick={() => setBulk("deny")}>
                <IconX size={13} /> Deny {chosen.length}
              </Button>
              <button type="button" className="text-accent hover:underline" onClick={() => setSelected(new Set())}>
                Clear
              </button>
            </>
          )}
          {progress && (
            <span className="tnum ml-auto text-muted">
              {progress.n} / {progress.of} done…
            </span>
          )}
        </div>
        <Q q={pending} rows={6}>
          {(d) =>
            queue.length === 0 ? (
              <Empty
                title={kind || department ? "Nothing waiting that matches" : "Inbox zero"}
                hint="New requests from the employee portal land here within a few seconds."
                icon={<IconCheck size={22} />}
              />
            ) : (
              <>
                <ul className="divide-y divide-line">
                  {queue.map((r) => {
                    const stale = now - r.ts > STALE;
                    const p = lk.people[r.principal];
                    const on = selected.has(r.id);
                    return (
                      <li key={r.id} className={cx("flex gap-3 px-4 py-3", on && "bg-accent/[0.05]")}>
                        <input
                          type="checkbox"
                          className="mt-1 h-4 w-4 shrink-0 accent-[rgb(var(--accent))]"
                          checked={on}
                          onChange={() => toggle(r.id)}
                          aria-label={`Select ${displayName(r)}'s request`}
                        />
                        <div className="flex min-w-0 flex-1 flex-col gap-2 lg:flex-row lg:items-start">
                          <div className="flex min-w-0 gap-3 lg:w-[230px] lg:shrink-0">
                            <Person r={r} />
                            {p && p.status !== "active" && <PersonStatusPill status={p.status} scale={p.budget_scale} />}
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-2 text-sm">
                              <Pill tone={KIND[r.kind]?.tone ?? "neutral"}>{KIND[r.kind]?.label ?? r.kind}</Pill>
                              <RequestDetail r={r} lk={lk} />
                            </div>
                            <div className="mt-1 break-words text-xs text-ink2">“{r.reason || "no reason given"}”</div>
                          </div>
                          <div className="flex shrink-0 items-center justify-between gap-3 lg:flex-col lg:items-end">
                            <span className={cx("tnum text-xs", stale ? "font-semibold text-warn" : "text-muted")} title={dateTime(r.ts)}>
                              waiting {age(r.ts, now)}
                            </span>
                            <span className="flex gap-1.5">
                              <Button size="sm" variant="good" onClick={() => setDeciding({ r, d: "approve" })}>
                                <IconCheck size={13} /> Approve
                              </Button>
                              <Button size="sm" onClick={() => setDeciding({ r, d: "deny" })}>
                                <IconX size={13} /> Deny
                              </Button>
                            </span>
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ul>
                <Pager page={f.page} size={PENDING_PAGE} shown={d.rows.length} total={d.total} hasMore={d.hasMore} onPage={f.setPage} fetching={pending.isPlaceholderData} className="border-t border-line" />
              </>
            )
          }
        </Q>
      </Card>

      <Card
        title="History"
        subtitle="Decided requests, newest first"
        flush
        actions={
          <Segmented
            value={hStatus}
            onChange={(v) => h.set({ status: v })}
            options={[
              { value: "", label: "All" },
              { value: "approved", label: "Approved" },
              { value: "denied", label: "Denied" },
            ]}
          />
        }
      >
        <Q q={history} rows={6}>
          {(d) =>
            decided.length === 0 ? (
              <Empty title={kind || department || hStatus ? "No decisions match the filters" : "No decisions yet"} />
            ) : (
              <>
                <TableWrap>
                  <table className="tbl min-w-[860px]">
                    <thead>
                      <tr>
                        <th>Person</th>
                        <th>Asked for</th>
                        <th>Reason</th>
                        <th>Decision</th>
                        <th>Note</th>
                        <th>When</th>
                      </tr>
                    </thead>
                    <tbody>
                      {decided.map((r) => (
                        <tr key={r.id}>
                          <td className="max-w-[200px]">
                            <Person r={r} />
                          </td>
                          <td className="text-xs">
                            <Pill tone={KIND[r.kind]?.tone ?? "neutral"}>{KIND[r.kind]?.label ?? r.kind}</Pill>
                            <div className="mt-1 text-ink">
                              {r.kind === "grant" ? (
                                <>
                                  {lk.resources[r.detail?.resource ?? ""]?.title ?? r.detail?.resource}
                                  {r.detail?.minutes ? <span className="text-muted"> · {minutes(r.detail.minutes)}</span> : null}
                                </>
                              ) : (
                                <span className="font-mono">{requestWhat(r)}</span>
                              )}
                            </div>
                          </td>
                          <td className="max-w-[220px] text-xs text-ink2">
                            <span className="line-clamp-2" title={r.reason}>
                              {r.reason}
                            </span>
                          </td>
                          <td>
                            <RequestStatusPill status={r.status} />
                            <div className="mt-1 text-[11px] text-muted">by {r.decided_by ?? "—"}</div>
                          </td>
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
                <Pager page={h.page} size={HISTORY_PAGE} shown={d.rows.length} total={d.total} hasMore={d.hasMore} onPage={h.setPage} fetching={history.isPlaceholderData} className="border-t border-line" />
              </>
            )
          }
        </Q>
      </Card>

      <ReasonDialog
        open={!!deciding}
        onClose={() => setDeciding(null)}
        title={deciding ? `${deciding.d === "approve" ? "Approve" : "Deny"} ${displayName(deciding.r)}'s request` : ""}
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
          setBulkDone(null);
          setDone(cur);
          setSelected((s) => {
            const n = new Set(s);
            n.delete(cur.r.id);
            return n;
          });
          await qc.invalidateQueries({ queryKey: ["admin"] });
        }}
      />

      <ReasonDialog
        open={!!bulk}
        onClose={() => setBulk(null)}
        title={bulk ? `${bulk === "approve" ? "Approve" : "Deny"} ${chosen.length} request${chosen.length === 1 ? "" : "s"}` : ""}
        description={
          bulk && (
            <div className="space-y-2">
              <BulkSummary rows={chosen} />
              {bulk === "approve" && (
                <div className="rounded-md bg-good/10 px-3 py-2 text-xs text-good">
                  Each person gets what they asked for right away: workflows on their menu, budgets scaled, grants live with their own expiry.
                </div>
              )}
            </div>
          )
        }
        reasonLabel="One note for everyone"
        reasonHint="Each employee sees this note with your name; it is kept on every request."
        reasonRequired={bulk === "deny"}
        placeholder={bulk === "deny" ? "Why not, and what to do instead" : "e.g. Approved in the weekly access review"}
        confirmLabel={bulk ? `${bulk === "approve" ? "Approve" : "Deny"} ${chosen.length}` : ""}
        tone={bulk === "approve" ? "good" : "danger"}
        canConfirm={chosen.length > 0}
        onConfirm={(note) => runBulk(bulk!, note)}
      />
    </div>
  );
}

/** What a batch contains, counted: "8 access grants (Prod DB ×5, Deploy ×3) · 4 workflows · 2 budget raises". */
const BATCH_ONE: Record<string, string> = { grant: "access grant", workflow: "workflow", quota: "budget raise" };
const BATCH_MANY: Record<string, string> = { grant: "access grants", workflow: "workflows", quota: "budget raises" };

function BulkSummary({ rows }: { rows: OrgRequest[] }) {
  const byKind = new Map<string, OrgRequest[]>();
  for (const r of rows) byKind.set(r.kind, [...(byKind.get(r.kind) ?? []), r]);
  const what = (r: OrgRequest) => (r.kind === "grant" ? (r.detail?.resource ?? "?") : r.kind === "workflow" ? (r.workflow ?? "?") : `${r.scale ?? 2}× budget`);
  const depts = new Set(rows.map((r) => r.department).filter(Boolean));
  return (
    <ul className="space-y-1 text-xs">
      {[...byKind].map(([k, rs]) => {
        const counts = new Map<string, number>();
        for (const r of rs) counts.set(what(r), (counts.get(what(r)) ?? 0) + 1);
        return (
          <li key={k} className="flex flex-wrap items-center gap-1.5">
            <Pill tone={KIND[k]?.tone ?? "neutral"}>
              {rs.length} {(rs.length === 1 ? BATCH_ONE : BATCH_MANY)[k] ?? k}
            </Pill>
            <span className="text-ink2">
              {[...counts]
                .sort((a, b) => b[1] - a[1])
                .slice(0, 5)
                .map(([w, n]) => (n > 1 ? `${w} (${n})` : w))
                .join(", ")}
            </span>
          </li>
        );
      })}
      <li className="text-muted">
        from {rows.length <= 5 ? rows.map(displayName).join(", ") : `${new Set(rows.map((r) => r.principal)).size} people`}
        {depts.size ? ` in ${depts.size} department${depts.size === 1 ? "" : "s"}` : ""}
      </li>
    </ul>
  );
}

function Banner({ tone, onClose, children }: { tone: "good" | "warn" | "neutral"; onClose: () => void; children: ReactNode }) {
  return (
    <div
      className={cx(
        "flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg px-4 py-2.5 text-sm ring-1 ring-inset",
        tone === "good" ? "bg-good/10 text-good ring-good/25" : tone === "warn" ? "bg-warn/10 text-warn ring-warn/30" : "bg-ink/[0.05] text-ink2 ring-line",
      )}
      role="status"
    >
      {children}
      <button type="button" onClick={onClose} className="text-xs opacity-70 hover:opacity-100" aria-label="Dismiss">
        <IconX size={14} />
      </button>
    </div>
  );
}
