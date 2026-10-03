import { Link } from "react-router-dom";
import type { ActivityEvent, AdminAction } from "../../api";
import { ago, dateTime, timeOfDay } from "../../lib/format";
import { describe, kindLabel } from "../../lib/events";
import { isAuto, responseLabel, ruleLabel } from "../../lib/security";
import { DecisionPill, SeverityPill, SourceBadge } from "../pills";
import { Pill, cx } from "../ui";

export type TimelineItem = { t: "event"; ts: number; e: ActivityEvent } | { t: "action"; ts: number; a: AdminAction };

/** Events and admin actions in one story, oldest first. */
export function buildTimeline(events: ActivityEvent[], actions: AdminAction[]): TimelineItem[] {
  return [
    ...events.map((e) => ({ t: "event" as const, ts: e.ts, e })),
    ...actions.map((a) => ({ t: "action" as const, ts: a.ts, a })),
  ].sort((x, y) => x.ts - y.ts || (x.t === "event" ? -1 : 1));
}

/** An event the detection named. The detection's own "incident" event carries the last evidence id; it is not evidence. */
export const isEvidence = (e: ActivityEvent) => !!e.evidence && e.source !== "detections";

function offset(ts: number, from: number): string {
  const d = Math.round(ts - from);
  const a = Math.abs(d);
  const s = a < 60 ? `${a}s` : a < 3600 ? `${Math.round(a / 60)}m` : `${(a / 3600).toFixed(1)}h`;
  return d === 0 ? "T" : `T${d < 0 ? "−" : "+"}${s}`;
}

function When({ ts, origin }: { ts: number; origin: number }) {
  const today = Date.now() / 1000 - ts < 20 * 3600;
  return (
    <div className="tnum text-[11px] leading-tight" title={dateTime(ts)}>
      <div className="font-medium text-ink2">{today ? timeOfDay(ts) : dateTime(ts)}</div>
      <div className="text-muted">{ago(ts)}</div>
      <div className="font-mono text-[10px] text-muted/80">{offset(ts, origin)}</div>
    </div>
  );
}

function Chip({ k, v }: { k: string; v: string | null | undefined }) {
  if (!v) return null;
  return (
    <span className="inline-flex max-w-full items-center gap-1 rounded bg-ink/[0.05] px-1.5 py-px text-[10.5px] text-ink2">
      <span className="text-muted">{k}</span>
      <span className="truncate font-mono">{v}</span>
    </span>
  );
}

function EventRow({ e, incidentId, origin }: { e: ActivityEvent; incidentId: string; origin: number }) {
  const ev = isEvidence(e);
  const cc = e.source === "claude_code";
  const detection = e.source === "detections" && e.kind === "incident";
  const otherInc = detection ? ((e.detail as { incident?: string } | null)?.incident ?? null) : null;
  const self = detection && otherInc === incidentId;
  const reason = (e.detail as { reason?: string } | null)?.reason;
  return (
    <li
      className={cx(
        "relative grid grid-cols-[64px_minmax(0,1fr)] gap-x-3 border-l-[3px] py-2.5 pl-3 pr-3 sm:grid-cols-[92px_minmax(0,1fr)_auto]",
        ev ? "border-l-bad bg-bad/[0.08]" : self ? "border-l-accent bg-accent/[0.07]" : detection ? "border-l-serious/60" : cc ? "border-l-cc/60 bg-cc/[0.04]" : "border-l-transparent",
      )}
    >
      <When ts={e.ts} origin={origin} />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <SourceBadge source={e.source} />
          {detection ? (
            <span className="text-xs font-semibold text-ink">
              {self ? "This incident raised" : "Incident raised"}: {ruleLabel(e.decision ?? "")}
            </span>
          ) : (
            <span className={cx("text-xs font-semibold", cc ? "text-cc" : "text-ink")}>{kindLabel(e.kind)}</span>
          )}
          {ev && (
            <Pill tone="bad" className="uppercase tracking-wide">
              evidence
            </Pill>
          )}
          {detection && !self && otherInc && (
            <Link to={`/console/incidents/${encodeURIComponent(otherInc)}`} className="text-[11px] font-medium text-accent hover:underline">
              open →
            </Link>
          )}
        </div>
        <div className={cx("mt-0.5 break-words text-xs", ev ? "text-ink" : "text-ink2")}>
          {detection ? (e.detail as { detail?: string } | null)?.detail : reason ?? describe(e)}
        </div>
        <div className="mt-1 flex flex-wrap gap-1">
          {!detection && <Chip k="model" v={e.model} />}
          <Chip k="tool" v={e.tool} />
          <Chip k="workflow" v={e.workflow === "(none)" ? "unattributed" : e.workflow} />
          <Chip k="resource" v={e.resource && e.resource !== e.model ? e.resource : null} />
          <Chip k="task" v={e.task} />
          <Chip k="client" v={e.client} />
        </div>
        <div className="mt-1 flex flex-wrap gap-1 sm:hidden">
          <DecisionPill decision={detection ? null : e.decision} />
          {e.severity && e.severity !== "info" && <SeverityPill severity={e.severity} />}
        </div>
      </div>
      <div className="hidden flex-col items-end gap-1 sm:flex">
        {!detection && <DecisionPill decision={e.decision} />}
        {e.severity && e.severity !== "info" && <SeverityPill severity={e.severity} />}
      </div>
    </li>
  );
}

function ActionRow({ a, origin }: { a: AdminAction; origin: number }) {
  const auto = isAuto(a);
  const strong = /quarantine|revoke|tighten|restrict/.test(a.action) || (a.detail as { status?: string } | null)?.status;
  return (
    <li
      className={cx(
        "grid grid-cols-[64px_minmax(0,1fr)] gap-x-3 border-l-[3px] py-2.5 pl-3 pr-3 sm:grid-cols-[92px_minmax(0,1fr)_auto]",
        auto ? "border-l-serious bg-serious/[0.07]" : "border-l-info/70 bg-info/[0.05]",
      )}
    >
      <When ts={a.ts} origin={origin} />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <span
            className={cx(
              "rounded px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-wide ring-1 ring-inset",
              auto ? "bg-serious/10 text-serious ring-serious/30" : "bg-info/10 text-info ring-info/25",
            )}
          >
            {auto ? "Auto response" : "Admin"}
          </span>
          <span className="text-xs font-semibold text-ink">{responseLabel(a)}</span>
          <span className="text-[11px] text-muted">by {a.actor}</span>
        </div>
        {a.reason && <div className="mt-0.5 break-words text-xs text-ink2">“{a.reason}”</div>}
      </div>
      <div className="hidden items-start sm:flex">{strong ? <Pill tone="bad">access changed</Pill> : null}</div>
    </li>
  );
}

export function EvidenceTimeline({ items, incidentId, origin }: { items: TimelineItem[]; incidentId: string; origin: number }) {
  return (
    <ol className="divide-y divide-line/60">
      {items.map((it) =>
        it.t === "event" ? (
          <EventRow key={`e-${it.e.id}`} e={it.e} incidentId={incidentId} origin={origin} />
        ) : (
          <ActionRow key={`a-${it.a.ts}-${it.a.action}-${it.a.actor}`} a={it.a} origin={origin} />
        ),
      )}
    </ol>
  );
}
