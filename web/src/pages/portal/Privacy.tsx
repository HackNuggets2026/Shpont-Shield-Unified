import { AdminLog } from "../../components/AdminLog";
import { IncidentStatusPill, LevelPill, SeverityPill } from "../../components/pills";
import { Card, Empty, ErrorBox, Loading, PageHeader, Pill } from "../../components/ui";
import { IconEye } from "../../components/icons";
import { ago, dateTime, titleCase } from "../../lib/format";
import { RiskBar } from "../console/People";
import { useMe } from "./Home";

export function PortalPrivacy() {
  const q = useMe();
  if (q.isPending) return <Loading rows={8} />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const s = q.data;
  const views = s.admin_activity.filter((a) => a.action === "view_events");

  return (
    <div className="space-y-4">
      <PageHeader title="Privacy" subtitle="What is collected about you, how you are scored, and every time an admin acted on or looked at your data." />

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="What is collected">
          <ul className="space-y-2">
            {s.collected.map((c) => (
              <li key={c} className="flex gap-2 text-sm text-ink2">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                {c}
              </li>
            ))}
          </ul>
          <p className="mt-4 text-xs text-muted">
            Admins see usage metadata (who, what, cost). Reading the content of your events requires them to state a reason, which appears below.
          </p>
        </Card>

        <Card title="Your risk score">
          {s.risk ? (
            <div>
              <div className="flex flex-wrap items-center gap-3">
                <RiskBar score={s.risk.score} />
                <LevelPill level={s.risk.level} />
              </div>
              <p className="mt-2 text-xs text-muted">
                Detections add weight when usage looks like an attack (probing, exfiltration, a new client on your key). The score decays over time; past
                set levels it can alert security, tighten your budget, or pause access.
              </p>
              <div className="mt-4">
                {s.risk.incidents.length === 0 ? (
                  <div className="text-sm text-muted">No incidents on record.</div>
                ) : (
                  <ul className="divide-y divide-line/60 rounded-lg border border-line">
                    {s.risk.incidents.map((i) => (
                      <li key={i.id} className="px-3 py-2.5 text-sm">
                        <div className="flex flex-wrap items-center gap-2">
                          <SeverityPill severity={i.severity} />
                          <span className="font-medium text-ink">{titleCase(i.rule)}</span>
                          <IncidentStatusPill status={i.status} />
                          <span className="ml-auto text-[11px] text-muted" title={dateTime(i.ts)}>
                            {ago(i.ts)}
                          </span>
                        </div>
                        <div className="mt-1 text-xs text-ink2">{i.detail}</div>
                        {i.note && <div className="mt-0.5 text-[11px] text-muted">Note: {i.note}</div>}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          ) : (
            <Empty title="Not shown" hint="Your company's policy does not show risk scores to employees." />
          )}
        </Card>
      </div>

      <Card
        title={
          <span className="flex items-center gap-2">
            <IconEye size={15} /> Content views
          </span>
        }
        subtitle="Each time an admin opened the content of your events, with their stated reason"
        actions={<Pill tone={views.length ? "cc" : "neutral"}>{views.length}</Pill>}
        flush
      >
        <AdminLog actions={views} showTarget={false} empty="Nobody has viewed the content of your events" />
      </Card>

      <Card title="Every admin action about you" flush>
        <AdminLog actions={s.admin_activity} showTarget={false} empty="No admin has acted on your account" />
      </Card>
    </div>
  );
}
