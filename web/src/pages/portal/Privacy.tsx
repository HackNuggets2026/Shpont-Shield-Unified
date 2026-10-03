import { useState } from "react";
import type { Incident, MeSummary } from "../../api";
import { me } from "../../api";
import { IncidentStatusPill, SeverityPill } from "../../components/pills";
import { Button, Card, Empty, ErrorBox, Loading, PageHeader, Pill, cx } from "../../components/ui";
import { IconCheck, IconDownload, IconEye, IconX } from "../../components/icons";
import { RULE_TEXT, actorLabel, ruleTitle } from "../../components/portal/explain";
import { MyAdminLog } from "../../components/portal/widgets";
import { ago, dateTime } from "../../lib/format";
import { useMe } from "./Home";

const NOT_COLLECTED = [
  "The text of prompts that were blocked. Only the reason is kept.",
  "Your Claude Code prompts, even if prompt logging is switched on in Claude Code",
  "Claude Code tool parameters such as file paths and shell commands",
  "Unmasked personal data or secrets: detected spans are masked and only a fingerprint (SHA-256) of the original is kept",
  "Anything outside AI calls: no keystrokes, screenshots, browsing or files",
];

const LEVEL: Record<string, { label: string; text: string; tone: string }> = {
  none: { label: "Normal", text: "Nothing needs attention.", tone: "bg-good" },
  alert: { label: "Security notified", text: "Security was alerted to take a look. Nothing about your access changed.", tone: "bg-warn" },
  tighten: { label: "Budget reduced", text: "Your daily budget was reduced automatically until someone reviews it.", tone: "bg-serious" },
  quarantine: { label: "Access paused", text: "Your access was paused automatically until someone reviews it.", tone: "bg-bad" },
};

function RiskCard({ risk }: { risk: NonNullable<MeSummary["risk"]> }) {
  const lv = LEVEL[risk.level] ?? { label: risk.level, text: "", tone: "bg-muted" };
  const open = risk.incidents.filter((i) => i.status === "open" || i.status === "acknowledged");
  const closed = risk.incidents.filter((i) => !(i.status === "open" || i.status === "acknowledged"));
  return (
    <Card title="Your risk score" subtitle="You see the same score and findings security sees">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
        <div className="tnum text-3xl font-semibold tracking-tight text-ink">{Math.round(risk.score)}</div>
        <div className="min-w-0 flex-1 pb-1">
          <div className="text-sm font-medium text-ink">{lv.label}</div>
          <div className="text-xs text-muted">{lv.text}</div>
        </div>
      </div>
      <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-ink/10" role="meter" aria-valuenow={risk.score} aria-valuemin={0} aria-valuemax={120}>
        <div className={cx("h-full rounded-full", lv.tone)} style={{ width: `${Math.max(Math.min(risk.score, 120) / 1.2, risk.score > 0 ? 2 : 0)}%` }} />
      </div>
      <p className="mt-3 text-xs text-muted">
        Automatic detections add points when usage looks risky (for example repeated blocked attempts, a sudden spike, or your key used from a new
        place). Points fade by half every couple of hours without new findings. Higher levels can alert security, reduce your budget or pause access
        until a person reviews it.
      </p>
      <div className="mt-4">
        {risk.incidents.length === 0 ? (
          <div className="flex items-center gap-2 text-sm text-ink2">
            <IconCheck size={15} className="text-good" /> No findings on record.
          </div>
        ) : (
          <div className="space-y-3">
            {open.length > 0 && <IncidentGroup title={`Open (${open.length})`} items={open} />}
            {closed.length > 0 && <IncidentGroup title={`Closed (${closed.length})`} items={closed} />}
          </div>
        )}
      </div>
    </Card>
  );
}

function IncidentGroup({ title, items }: { title: string; items: Incident[] }) {
  return (
    <div>
      <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">{title}</div>
      <ul className="divide-y divide-line/60 rounded-lg border border-line">
        {items.map((i) => (
          <li key={i.id} className="px-3 py-2.5 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-ink">{ruleTitle(i.rule)}</span>
              <SeverityPill severity={i.severity} />
              <IncidentStatusPill status={i.status} />
              <span className="ml-auto text-[11px] text-muted" title={dateTime(i.ts)}>
                {ago(i.ts)}
              </span>
            </div>
            {RULE_TEXT[i.rule] && <div className="mt-1 text-xs text-ink2">{RULE_TEXT[i.rule].text}</div>}
            <div className="mt-1 break-words text-[11px] text-muted">
              What triggered it: {i.detail} · +{Math.round(i.weight)} points
            </div>
            {i.note && <div className="mt-0.5 text-[11px] text-ink2">Reviewer note: “{i.note}”</div>}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** GDPR-style copy of what the portal shows about me, assembled in the browser. */
function DownloadButton() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const [summary, grants, activity] = await Promise.all([me.summary(), me.grants(), me.activity(500)]);
      const blob = new Blob([JSON.stringify({ exported_at: new Date().toISOString(), summary, grants, recent_activity: activity }, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `my-ai-usage-${summary.principal}-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-2">
      <Button onClick={run} disabled={busy}>
        <IconDownload size={14} />
        {busy ? "Preparing…" : "Download my data"}
      </Button>
      {error != null && <ErrorBox error={error} compact />}
    </div>
  );
}

export function PortalPrivacy() {
  const q = useMe();
  if (q.isPending) return <Loading rows={8} />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const s = q.data;
  const views = s.admin_activity.filter((a) => a.action === "view_events");
  const changes = s.admin_activity.filter((a) => a.action !== "view_events");

  return (
    <div className="space-y-4">
      <PageHeader
        title="Privacy"
        subtitle="What is collected about you, what is not, how you are scored, and every time someone acted on or looked at your data."
        actions={<DownloadButton />}
      />

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="What is collected">
          <ul className="space-y-2">
            {s.collected.map((c) => (
              <li key={c} className="flex gap-2 text-sm text-ink2">
                <IconCheck size={14} className="mt-0.5 shrink-0 text-accent" />
                <span>{c[0].toUpperCase() + c.slice(1)}</span>
              </li>
            ))}
          </ul>
          <div className="mb-2 mt-5 text-xs font-semibold text-ink">Never collected</div>
          <ul className="space-y-2">
            {NOT_COLLECTED.map((c) => (
              <li key={c} className="flex gap-2 text-sm text-ink2">
                <IconX size={14} className="mt-0.5 shrink-0 text-muted" />
                <span>{c}</span>
              </li>
            ))}
          </ul>
          <p className="mt-5 rounded-lg bg-raised/60 px-3 py-2.5 text-xs text-ink2">
            Admins see usage metadata: who, which tool or model, cost and the policy decision. To open the (masked) content of your events they must
            state a reason. Each time they do, it is listed below with that reason, and you can download a copy of everything on this portal.
          </p>
        </Card>

        {s.risk ? (
          <RiskCard risk={s.risk} />
        ) : (
          <Card title="Your risk score">
            <Empty title="Not shown" hint="Your company's policy does not show risk scores to employees." />
          </Card>
        )}
      </div>

      <Card
        title={
          <span className="flex items-center gap-2">
            <IconEye size={15} /> Who looked at the content of your events
          </span>
        }
        subtitle="Each time an admin opened your event content, with their stated reason"
        actions={<Pill tone={views.length ? "cc" : "neutral"}>{views.length === 0 ? "nobody" : `${views.length} time${views.length === 1 ? "" : "s"}`}</Pill>}
        flush
      >
        <MyAdminLog actions={views} empty="Nobody has opened the content of your events" />
      </Card>

      <Card
        title="Every change made to your access"
        subtitle={
          changes.length
            ? `${changes.length} action${changes.length === 1 ? "" : "s"} by admins and automatic rules, newest first. Latest: ${actorLabel(changes[0]?.actor)}.`
            : "By admins and automatic rules"
        }
        flush
      >
        <MyAdminLog actions={changes} empty="Nobody has changed your access" />
      </Card>
    </div>
  );
}
