import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { admin } from "../../api";
import { AdminLog } from "../../components/AdminLog";
import { FeedRow } from "../../components/ActivityFeed";
import { ReasonDialog } from "../../components/Dialog";
import { IncidentStatusPill, LevelPill, PersonStatusPill, SeverityPill } from "../../components/pills";
import { RestrictActions } from "../../components/RestrictActions";
import { Button, Card, Empty, ErrorBox, Loading, PageHeader, Segmented, Stat } from "../../components/ui";
import { IconArrowLeft } from "../../components/icons";
import { ago, dateTime, titleCase } from "../../lib/format";
import { RiskBar } from "./People";

type Next = "acknowledged" | "resolved" | "dismissed" | "open";

const NEXT_COPY: Record<Next, { label: string; body: string; tone: "primary" | "good" | "danger" }> = {
  acknowledged: { label: "Acknowledge", body: "Mark that someone is looking at this. The risk score still counts it.", tone: "primary" },
  resolved: { label: "Resolve", body: "The issue was real and has been dealt with.", tone: "good" },
  dismissed: { label: "Dismiss", body: "A false positive. Its weight stops counting toward the person's risk.", tone: "primary" },
  open: { label: "Reopen", body: "Put the incident back in the open queue.", tone: "primary" },
};

export function IncidentPage() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin", "incident", id], queryFn: () => admin.incident(id), refetchInterval: 10_000 });
  const people = useQuery({ queryKey: ["admin", "principals"], queryFn: admin.principals });
  const [next, setNext] = useState<Next | null>(null);
  const [only, setOnly] = useState<"all" | "evidence">("all");

  const back = (
    <Link to="/console/security" className="inline-flex items-center gap-1 hover:text-ink">
      <IconArrowLeft size={12} /> Security
    </Link>
  );
  if (q.isPending)
    return (
      <div>
        <PageHeader title="Incident" back={back} />
        <Loading rows={8} />
      </div>
    );
  if (q.isError)
    return (
      <div>
        <PageHeader title="Incident" back={back} />
        <ErrorBox error={q.error} retry={() => q.refetch()} />
      </div>
    );

  const { incident: inc, timeline, principal, actions } = q.data;
  const row = people.data?.find((r) => r.principal === principal.id);
  const evidenceCount = timeline.filter((e) => e.evidence).length;
  const rows = only === "evidence" ? timeline.filter((e) => e.evidence) : timeline;
  const transitions: Next[] =
    inc.status === "open" ? ["acknowledged", "resolved", "dismissed"] : inc.status === "acknowledged" ? ["resolved", "dismissed"] : ["open"];

  return (
    <div className="space-y-4">
      <PageHeader
        back={back}
        title={
          <span className="flex flex-wrap items-center gap-2">
            {titleCase(inc.rule)}
            <SeverityPill severity={inc.severity} />
            <IncidentStatusPill status={inc.status} />
          </span>
        }
        subtitle={
          <span>
            <span className="font-mono">{inc.id}</span> · {dateTime(inc.ts)} ({ago(inc.ts)})
          </span>
        }
        actions={transitions.map((t) => (
          <Button key={t} variant={t === "resolved" ? "good" : "secondary"} onClick={() => setNext(t)}>
            {NEXT_COPY[t].label}
          </Button>
        ))}
      />

      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="What was detected" className="xl:col-span-2">
          <p className="text-sm text-ink">{inc.detail}</p>
          <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="Rule" value={inc.rule} />
            <Stat label="Weight" value={Math.round(inc.weight)} />
            <Stat label="Evidence events" value={`${inc.evidence.length} named · ${evidenceCount} found`} />
            <Stat label="Status" value={inc.status} />
          </div>
          {inc.note && (
            <div className="mt-4 rounded-lg bg-raised px-3 py-2 text-xs text-ink2">
              <span className="font-medium text-ink">Note:</span> {inc.note}
            </div>
          )}
        </Card>
        <Card
          title="Person"
          actions={
            <Link to={`/console/people/${encodeURIComponent(principal.id)}`} className="text-xs font-medium text-accent hover:underline">
              Open profile →
            </Link>
          }
        >
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <span className="text-base font-semibold text-ink">{principal.id}</span>
            <PersonStatusPill status={principal.status} scale={row?.budget_scale} />
            <LevelPill level={principal.level} />
          </div>
          <div className="mb-4">
            <div className="mb-1 text-[11px] uppercase tracking-wide text-muted">Risk score</div>
            <RiskBar score={principal.risk} />
          </div>
          <RestrictActions pid={principal.id} status={principal.status} scale={row?.budget_scale ?? 1} compact />
        </Card>
      </div>

      <Card
        flush
        title="Evidence timeline"
        subtitle="The events the detection names (highlighted) and what the person did from an hour before to 15 minutes after"
        actions={
          <Segmented
            value={only}
            onChange={setOnly}
            options={[
              { value: "all", label: `All (${timeline.length})` },
              { value: "evidence", label: `Evidence (${evidenceCount})` },
            ]}
          />
        }
      >
        {rows.length === 0 ? (
          <Empty title="No events in the window" hint="The evidence may have aged out of the activity store." />
        ) : (
          <ul className="max-h-[560px] overflow-y-auto">
            {rows.map((e) => (
              <FeedRow key={e.id} e={e} />
            ))}
          </ul>
        )}
      </Card>

      <Card title="Admin actions since the incident" flush>
        <AdminLog actions={actions} showTarget={false} empty="Nobody has acted on this person since the incident" />
      </Card>

      <ReasonDialog
        open={!!next}
        onClose={() => setNext(null)}
        title={next ? `${NEXT_COPY[next].label} incident` : ""}
        description={next ? NEXT_COPY[next].body : ""}
        reasonLabel="Note"
        reasonHint="Kept on the incident and in the admin log."
        reasonRequired={next === "resolved" || next === "dismissed"}
        confirmLabel={next ? NEXT_COPY[next].label : ""}
        tone={next ? NEXT_COPY[next].tone : "primary"}
        onConfirm={async (note) => {
          await admin.setIncident(inc.id, next!, note);
          await qc.invalidateQueries({ queryKey: ["admin"] });
        }}
      />
    </div>
  );
}
