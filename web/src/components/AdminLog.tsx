import type { AdminAction } from "../api";
import { ago, dateTime, titleCase } from "../lib/format";
import { Empty, Pill, type Tone } from "./ui";

const actionTone = (a: string): Tone =>
  /quarantine|revoke|restrict|tighten|disable/.test(a)
    ? "bad"
    : /view_events/.test(a)
      ? "cc"
      : /clear|restore|approve|grant|resolved/.test(a)
        ? "good"
        : "neutral";

export function actionLabel(a: AdminAction): string {
  const d = a.detail as Record<string, unknown> | null;
  if (a.action === "restrict" && d) {
    if (d.status && d.status !== "active") return `Set status ${d.status}`;
    if (typeof d.budget_scale === "number") return `Budget set to ${Math.round(d.budget_scale * 100)}%`;
    if (d.approved_workflows) return "Workflows approved";
  }
  if (a.action === "view_events") return "Viewed event content";
  if (a.action === "clear_restrictions") return "Restrictions lifted";
  return titleCase(a.action);
}

/** Admin actions, newest first: who did what to whom, and why. */
export function AdminLog({ actions, showTarget = true, empty = "No admin actions recorded" }: { actions: AdminAction[]; showTarget?: boolean; empty?: string }) {
  if (!actions.length) return <Empty title={empty} />;
  const rows = [...actions].sort((a, b) => b.ts - a.ts);
  return (
    <ol className="divide-y divide-line/60">
      {rows.map((a, i) => (
        <li key={`${a.ts}-${i}`} className="flex gap-3 px-4 py-2.5 text-sm">
          <div className="w-16 shrink-0 pt-0.5 text-xs text-muted" title={dateTime(a.ts)}>
            {ago(a.ts)}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <Pill tone={actionTone(a.action)}>{actionLabel(a)}</Pill>
              {showTarget && <span className="font-mono text-xs text-ink2">{a.target}</span>}
              <span className="text-xs text-muted">by {a.actor}</span>
            </div>
            {a.reason && <div className="mt-0.5 break-words text-xs text-ink2">“{a.reason}”</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}
