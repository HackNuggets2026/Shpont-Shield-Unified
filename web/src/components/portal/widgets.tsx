// Employee-portal building blocks: phone-friendly lists (no wide tables) and plain-language copy.
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { AccessRequest, AdminAction, BudgetScope, Grant, Lease, MeSummary } from "../../api";
import { ago, countdown, dateTime, minutes, pct, usd } from "../../lib/format";
import { useNow } from "../../lib/useNow";
import { RequestStatusPill } from "../pills";
import { Button, Chips, Empty, ErrorBox, Pill, cx, type Tone } from "../ui";
import { IconAlert, IconCheck, IconClock, IconEye, IconLock, IconStop, IconX } from "../icons";
import { actorLabel, isAutomatic, myActionKind, myActionLabel, statusReason } from "./explain";

// ---- time helpers -----------------------------------------------------------------------------

/** Seconds until the next 00:00 UTC, when daily budgets reset. */
export function untilUtcMidnight(now = Date.now() / 1000): number {
  const d = new Date(now * 1000);
  const next = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + 1) / 1000;
  return next - now;
}

export function inHours(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m} min`;
}

/** A budget scope that is used up (spend or tokens at or over its limit). */
export function exhausted(b: BudgetScope): boolean {
  return (b.usd_limit !== null && b.usd >= b.usd_limit) || (b.tokens_limit !== null && b.tokens >= b.tokens_limit);
}

// ---- status banner ----------------------------------------------------------------------------

function Line({ ok, children }: { ok: boolean; children: ReactNode }) {
  return (
    <li className="flex gap-2">
      {ok ? <IconCheck size={14} className="mt-0.5 shrink-0 text-good" /> : <IconX size={14} className="mt-0.5 shrink-0 text-muted" />}
      <span>{children}</span>
    </li>
  );
}

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-medium uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-0.5 text-sm text-ink">{children}</dd>
    </div>
  );
}

const FRAME: Record<string, string> = {
  serious: "bg-serious/[0.07] ring-serious/30",
  bad: "bg-bad/[0.07] ring-bad/30",
  warn: "bg-warn/[0.07] ring-warn/30",
};

/**
 * Where my access stands, who decided, since when and what still works. Written to inform, not alarm:
 * a quarantine is a pause for review, and the employee can see exactly why on the Privacy page.
 */
export function AccessStatus({ s, compact }: { s: MeSummary; compact?: boolean }) {
  const st = s.status;
  const now = Date.now() / 1000;
  const openIncidents = (s.risk?.incidents ?? []).filter((i) => i.status === "open" || i.status === "acknowledged").length;
  const usedUp = s.budgets.filter(exhausted);
  const who = actorLabel(st.by);
  const when = st.since ? (
    <span title={dateTime(st.since)}>
      {ago(st.since)} <span className="text-muted">· {dateTime(st.since)}</span>
    </span>
  ) : (
    "—"
  );
  const decided = (
    <>
      {who}
      {isAutomatic(st.by) && <div className="text-[11px] text-muted">No person made this call; an admin reviews it.</div>}
    </>
  );

  if (st.status === "quarantined" || st.status === "revoked") {
    const revoked = st.status === "revoked";
    return (
      <section className={cx("rounded-xl px-4 py-4 ring-1 ring-inset sm:px-5", FRAME[revoked ? "bad" : "serious"])} aria-label="Access status">
        <div className="flex gap-3">
          <IconLock className={cx("mt-0.5 shrink-0", revoked ? "text-bad" : "text-serious")} size={20} />
          <div className="min-w-0 flex-1">
            <h2 className="font-semibold text-ink">{revoked ? "Your AI access has been revoked" : "Your AI access is paused for a review"}</h2>
            <p className="mt-1 text-sm text-ink2">{statusReason(st.reason)}</p>
            {!compact && (
              <>
                <dl className="mt-3 grid gap-3 sm:grid-cols-3">
                  <Meta label="Decided by">{decided}</Meta>
                  <Meta label="Since">{when}</Meta>
                  <Meta label="Open findings">
                    {s.risk ? (
                      <Link to="/portal/privacy" className="text-accent hover:underline">
                        {openIncidents} open · see what was detected →
                      </Link>
                    ) : (
                      "—"
                    )}
                  </Meta>
                </dl>
                <div className="mt-4 grid gap-4 text-sm text-ink2 sm:grid-cols-2">
                  <div>
                    <div className="mb-1.5 text-xs font-semibold text-ink">Still works</div>
                    <ul className="space-y-1">
                      {!revoked && <Line ok>Prompts to approved models, at {pct(st.budget_scale, 0)} of your normal daily budget</Line>}
                      {!revoked && <Line ok>Read and search tools</Line>}
                      <Line ok>This portal: your usage, requests, and the record of who looked at what</Line>
                      <Line ok>Stopping anything still running in your name</Line>
                    </ul>
                  </div>
                  <div>
                    <div className="mb-1.5 text-xs font-semibold text-ink">Paused for now</div>
                    <ul className="space-y-1">
                      {revoked ? (
                        <Line ok={false}>All AI calls and tools under your key</Line>
                      ) : (
                        <>
                          <Line ok={false}>Tools that write, run, send or deploy</Line>
                          <Line ok={false}>Time-boxed access such as the production database (grants are suspended)</Line>
                          <Line ok={false}>Starting new simulators or VMs</Line>
                        </>
                      )}
                    </ul>
                  </div>
                </div>
                {!revoked && usedUp.length > 0 && (
                  <p className="mt-3 flex gap-2 rounded-lg bg-panel/70 px-3 py-2 text-xs text-ink2 ring-1 ring-inset ring-line">
                    <IconClock size={14} className="mt-px shrink-0 text-muted" />
                    Today&apos;s reduced allowance is already used, so new prompts are refused until budgets reset at 00:00 UTC (in{" "}
                    {inHours(untilUtcMidnight(now))}).
                  </p>
                )}
                <p className="mt-3 text-xs text-muted">
                  Nothing has been deleted. What was detected, and every time an admin looked at your data, is listed on your Privacy page. When the
                  review ends, access comes back right away. If you think this is a mistake, talk to your security team; they see the same findings
                  you do.
                </p>
              </>
            )}
          </div>
        </div>
      </section>
    );
  }

  if (st.budget_scale < 1) {
    return (
      <section className={cx("rounded-xl px-4 py-3.5 ring-1 ring-inset sm:px-5", FRAME.warn)} aria-label="Access status">
        <div className="flex gap-3">
          <IconAlert className="mt-0.5 shrink-0 text-warn" size={18} />
          <div className="min-w-0 flex-1">
            <h2 className="font-semibold text-ink">Limited: your daily budget is {pct(st.budget_scale, 0)} of normal</h2>
            <p className="mt-0.5 text-sm text-ink2">{statusReason(st.reason)}</p>
            {!compact && (
              <>
                <dl className="mt-3 grid gap-3 sm:grid-cols-3">
                  <Meta label="Decided by">{decided}</Meta>
                  <Meta label="Since">{when}</Meta>
                  <Meta label="What still works">Every workflow and tool you normally use, within the smaller budget</Meta>
                </dl>
                {usedUp.length > 0 && (
                  <p className="mt-3 text-xs text-ink2">
                    Today&apos;s reduced budget is used up; it resets at 00:00 UTC (in {inHours(untilUtcMidnight(now))}).
                  </p>
                )}
              </>
            )}
          </div>
        </div>
      </section>
    );
  }

  const extras = st.approved_workflows;
  return (
    <section className="flex items-start gap-3 rounded-xl bg-good/[0.07] px-4 py-3 ring-1 ring-inset ring-good/25" aria-label="Access status">
      <IconCheck className="mt-0.5 shrink-0 text-good" size={18} />
      <div className="min-w-0 text-sm">
        <span className="font-semibold text-ink">Active.</span>{" "}
        <span className="text-ink2">
          {st.budget_scale > 1 ? `Your daily budget is raised to ${pct(st.budget_scale, 0)} of normal` : "Full access with your normal daily budget"}
          {extras.length ? `, plus ${extras.join(", ")} (approved for you)` : ""}.
        </span>
        {st.budget_scale > 1 && st.by && (
          <span className="text-xs text-muted">
            {" "}
            Approved by {actorLabel(st.by)}
            {st.since ? ` ${ago(st.since)}` : ""}.
          </span>
        )}
        {usedUp.length > 0 && (
          <div className="mt-1 text-xs text-warn">
            A daily budget is used up ({usedUp.map((b) => (b.scope === "principal" ? "yours" : `team ${b.key}`)).join(", ")}); it resets at 00:00 UTC (in{" "}
            {inHours(untilUtcMidnight(now))}).
          </div>
        )}
      </div>
    </section>
  );
}

// ---- running resources ------------------------------------------------------------------------

export function LeaseList({
  leases,
  onStop,
  empty = "Nothing running in your name",
}: {
  leases: Lease[];
  onStop: (id: string) => Promise<{ ok: boolean; stopped: boolean }>;
  empty?: string;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const stop = async (id: string) => {
    setBusy(id);
    setError(null);
    setMsg(null);
    try {
      const r = await onStop(id);
      setMsg(r.stopped ? "Stopped. Billing for it has ended." : "Released. The gateway closed the lease but could not stop the machine itself; stop it in its own tool too.");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      {error != null && (
        <div className="p-3">
          <ErrorBox error={error} compact />
        </div>
      )}
      {msg && <div className="border-b border-line bg-good/10 px-4 py-2 text-xs text-good">{msg}</div>}
      {leases.length === 0 ? (
        <Empty title={empty} hint="Simulators, VMs and other paid resources you start through AI tools show up here." />
      ) : (
        <ul className="divide-y divide-line/60">
          {leases.map((l) => {
            const idle = l.flags.length > 0 || (l.idle_minutes ?? 0) >= 10;
            return (
              <li key={l.id} className={cx("flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3", idle && "bg-warn/[0.05]")}>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-ink">{l.resource}</span>
                    <span className="font-mono text-[11px] text-muted">{l.handle ?? l.id}</span>
                    {idle && (
                      <Pill tone="warn" dot>
                        idle {minutes(l.idle_minutes)}
                      </Pill>
                    )}
                  </div>
                  <div className="mt-0.5 text-xs text-muted">
                    {l.workflow ?? "no workflow"}
                    {l.task ? <span className="font-mono"> · {l.task}</span> : null} · started {ago(l.started)}
                  </div>
                </div>
                <div className="tnum text-right text-xs">
                  <div className="text-ink">{minutes(l.minutes)}</div>
                  <div className="text-muted">{usd(l.running_usd)} so far</div>
                </div>
                <Button size="sm" variant={idle ? "primary" : "secondary"} onClick={() => stop(l.id)} disabled={busy === l.id}>
                  <IconStop size={13} />
                  {busy === l.id ? "Stopping…" : "Stop"}
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// ---- grants -----------------------------------------------------------------------------------

export function GrantList({ grants, paused, empty = "You hold no grants right now" }: { grants: Grant[]; paused?: boolean; empty?: string }) {
  const now = useNow();
  if (!grants.length) return <Empty title={empty} hint="Request one below when a task needs a sensitive system. It ends on its own." />;
  const rows = [...grants].sort((a, b) => Number(b.live) - Number(a.live) || (b.expires ?? 0) - (a.expires ?? 0));
  return (
    <ul className="divide-y divide-line/60">
      {rows.map((g) => {
        const live = g.live && (!g.expires || g.expires > now);
        const soon = live && !!g.expires && g.expires - now < 600;
        return (
          <li key={g.id} className={cx("flex flex-wrap items-start gap-x-4 gap-y-2 px-4 py-3", !live && "opacity-60")}>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-ink">{g.title ?? g.resource}</span>
                <Chips items={g.actions} empty="all actions" />
                {g.workflow && <span className="text-[11px] text-muted">only in {g.workflow}</span>}
              </div>
              <div className="mt-0.5 text-xs text-muted">
                granted {ago(g.granted_at)} by {actorLabel(g.granted_by)}
                {g.reason ? <span className="text-ink2"> · “{g.reason}”</span> : null}
              </div>
            </div>
            <div className="shrink-0">
              {live && paused ? (
                <Pill tone="warn" dot title="Grants do not work while your access is paused">
                  suspended · {countdown(g.expires, now)} left
                </Pill>
              ) : live ? (
                <Pill tone={soon ? "warn" : "good"} dot>
                  <span className="tnum">{countdown(g.expires, now)} left</span>
                </Pill>
              ) : (
                <Pill tone="neutral">ended {ago(g.expires, now)}</Pill>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

// ---- requests ---------------------------------------------------------------------------------

export function requestLabel(r: AccessRequest, titles: Record<string, string> = {}): string {
  if (r.kind === "workflow") return `Use the ${r.workflow ?? "?"} workflow`;
  if (r.kind === "quota") return `Daily budget ×${r.scale ?? 2}`;
  const d = r.detail ?? {};
  const res = d.resource ? titles[d.resource] ?? d.resource : "access";
  return `${res}${d.minutes ? ` for ${minutes(d.minutes)}` : ""}`;
}

export function RequestList({
  requests,
  titles,
  limit,
  empty = "You have not asked for anything yet",
}: {
  requests: AccessRequest[];
  titles?: Record<string, string>;
  limit?: number;
  empty?: string;
}) {
  if (!requests.length) return <Empty title={empty} />;
  const rows = [...requests].sort((a, b) => Number(b.status === "pending") - Number(a.status === "pending") || b.ts - a.ts).slice(0, limit);
  return (
    <ul className="divide-y divide-line/60">
      {rows.map((r) => (
        <li key={r.id} className="px-4 py-3 text-sm">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="font-medium text-ink">{requestLabel(r, titles)}</div>
              <div className="mt-0.5 break-words text-xs text-ink2">You said: “{r.reason}”</div>
            </div>
            <RequestStatusPill status={r.status} />
          </div>
          <div className="mt-1 text-[11px] text-muted">
            {r.status === "pending" ? (
              <>asked {ago(r.ts)} · waiting for an admin</>
            ) : (
              <>
                {r.status} by {actorLabel(r.decided_by)} {ago(r.decided_at)}
                {r.note ? <span className="text-ink2"> · “{r.note}”</span> : null}
              </>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

// ---- admin actions about me -------------------------------------------------------------------

const KIND_TONE: Record<string, Tone> = { restrict: "serious", view: "cc", positive: "good", neutral: "neutral" };

export function MyAdminLog({ actions, empty = "No admin has acted on your account", limit }: { actions: AdminAction[]; empty?: string; limit?: number }) {
  if (!actions.length) return <Empty title={empty} hint="Anything an admin or an automatic rule does about you will be listed here, with the reason." />;
  const rows = [...actions].sort((a, b) => b.ts - a.ts).slice(0, limit);
  return (
    <ol className="divide-y divide-line/60">
      {rows.map((a, i) => {
        const kind = myActionKind(a);
        return (
          <li key={`${a.ts}-${i}`} className="flex gap-3 px-4 py-3 text-sm">
            <div className="w-14 shrink-0 pt-0.5 text-xs text-muted sm:w-16" title={dateTime(a.ts)}>
              {ago(a.ts)}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5">
                <Pill tone={KIND_TONE[kind]}>
                  {kind === "view" && <IconEye size={11} className="-ml-0.5 mr-0.5 inline" />}
                  {myActionLabel(a)}
                </Pill>
                <span className="text-xs text-muted">by {actorLabel(a.actor)}</span>
              </div>
              {a.reason ? (
                <div className="mt-1 break-words text-xs text-ink2">
                  <span className="text-muted">Stated reason: </span>“{a.reason}”
                </div>
              ) : (
                <div className="mt-1 text-xs text-muted">No reason given</div>
              )}
              <div className="mt-0.5 text-[11px] text-muted">{dateTime(a.ts)}</div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/** Like ui's Field, but a <div>: a <label> around several buttons would forward stray clicks to the first one. */
export function Group({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <div role="group">
      <div className="mb-1 block text-xs font-medium text-ink2">{label}</div>
      {children}
      {hint && <div className="mt-1 block text-[11px] text-muted">{hint}</div>}
    </div>
  );
}
