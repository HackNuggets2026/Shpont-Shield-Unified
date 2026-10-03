import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { admin, type AccessRequest } from "../../api";
import { ReasonDialog } from "../../components/Dialog";
import { RequestStatusPill } from "../../components/pills";
import { Button, Card, Empty, PageHeader, Pill, Q, TableWrap, type Tone } from "../../components/ui";
import { IconCheck, IconX } from "../../components/icons";
import { ago, dateTime, minutes } from "../../lib/format";

const KIND: Record<string, { label: string; tone: Tone }> = {
  workflow: { label: "Workflow", tone: "info" },
  quota: { label: "More budget", tone: "warn" },
  grant: { label: "Access grant", tone: "cc" },
};

export function requestWhat(r: AccessRequest): string {
  if (r.kind === "workflow") return r.workflow ?? "?";
  if (r.kind === "quota") return `${r.scale ?? 2}× daily budget`;
  const d = r.detail ?? {};
  return `${d.resource ?? "?"}${d.minutes ? ` for ${minutes(d.minutes)}` : ""}${d.actions?.length ? ` (${d.actions.join(", ")})` : ""}`;
}

export function Requests() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin", "requests"], queryFn: () => admin.requests(), refetchInterval: 5_000 });
  const [deciding, setDeciding] = useState<{ r: AccessRequest; d: "approve" | "deny" } | null>(null);

  return (
    <div className="space-y-4">
      <PageHeader title="Requests" subtitle="Employees ask for workflows that need approval, more budget, or time-boxed access." />
      <Q q={q} rows={6}>
        {(all) => {
          const pending = all.filter((r) => r.status === "pending").sort((a, b) => a.ts - b.ts);
          const done = all.filter((r) => r.status !== "pending").sort((a, b) => (b.decided_at ?? b.ts) - (a.decided_at ?? a.ts));
          return (
            <>
              <Card title={`Waiting for a decision (${pending.length})`} subtitle="Oldest first" flush>
                {pending.length === 0 ? (
                  <Empty title="Inbox zero" hint="New requests from the employee portal land here." icon={<IconCheck size={22} />} />
                ) : (
                  <ul className="divide-y divide-line">
                    {pending.map((r) => (
                      <li key={r.id} className="flex flex-col gap-3 px-4 py-3.5 sm:flex-row sm:items-center">
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <Pill tone={KIND[r.kind]?.tone ?? "neutral"}>{KIND[r.kind]?.label ?? r.kind}</Pill>
                            <Link to={`/console/people/${encodeURIComponent(r.principal)}`} className="font-semibold text-ink hover:text-accent">
                              {r.principal}
                            </Link>
                            <span className="text-sm text-ink2">wants</span>
                            <span className="font-mono text-sm text-ink">{requestWhat(r)}</span>
                          </div>
                          <div className="mt-1 break-words text-sm text-ink2">“{r.reason}”</div>
                          <div className="mt-0.5 text-[11px] text-muted" title={dateTime(r.ts)}>
                            asked {ago(r.ts)} · <span className="font-mono">{r.id}</span>
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
                    ))}
                  </ul>
                )}
              </Card>

              <Card title="History" flush>
                {done.length === 0 ? (
                  <Empty title="No decisions yet" />
                ) : (
                  <TableWrap>
                    <table className="tbl min-w-[820px]">
                      <thead>
                        <tr>
                          <th>Person</th>
                          <th>Kind</th>
                          <th>For</th>
                          <th>Reason</th>
                          <th>Decision</th>
                          <th>By</th>
                          <th>Note</th>
                          <th>When</th>
                        </tr>
                      </thead>
                      <tbody>
                        {done.map((r) => (
                          <tr key={r.id}>
                            <td className="font-medium">{r.principal}</td>
                            <td>
                              <Pill tone={KIND[r.kind]?.tone ?? "neutral"}>{KIND[r.kind]?.label ?? r.kind}</Pill>
                            </td>
                            <td className="font-mono text-xs">{requestWhat(r)}</td>
                            <td className="max-w-[220px] truncate text-xs" title={r.reason}>
                              {r.reason}
                            </td>
                            <td>
                              <RequestStatusPill status={r.status} />
                            </td>
                            <td className="text-xs">{r.decided_by ?? "—"}</td>
                            <td className="max-w-[200px] truncate text-xs text-ink2" title={r.note}>
                              {r.note || "—"}
                            </td>
                            <td className="whitespace-nowrap text-xs text-muted" title={dateTime(r.decided_at)}>
                              {ago(r.decided_at)}
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
            <span>
              {KIND[deciding.r.kind]?.label}: <span className="font-mono">{requestWhat(deciding.r)}</span>
              <br />
              <span className="text-muted">“{deciding.r.reason}”</span>
            </span>
          )
        }
        reasonLabel="Note to the employee"
        reasonHint="Shown to the employee with your name."
        reasonRequired={deciding?.d === "deny"}
        confirmLabel={deciding?.d === "approve" ? "Approve" : "Deny"}
        tone={deciding?.d === "approve" ? "good" : "danger"}
        onConfirm={async (note) => {
          await admin.decide(deciding!.r.id, deciding!.d, note);
          await qc.invalidateQueries({ queryKey: ["admin"] });
        }}
      />
    </div>
  );
}
