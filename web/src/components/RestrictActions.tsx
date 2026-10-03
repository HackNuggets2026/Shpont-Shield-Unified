import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { admin, type AuditEvent } from "../api";
import { dateTime, usd } from "../lib/format";
import { Dialog, ReasonDialog } from "./Dialog";
import { DecisionPill } from "./pills";
import { Button, Empty, Field, Pill, TableWrap } from "./ui";
import { IconEye, IconLock } from "./icons";

type Mode = "quarantine" | "revoke" | "tighten" | "restore";

const COPY: Record<Mode, { title: string; confirm: string; tone: "danger" | "primary" | "good"; body: string }> = {
  quarantine: {
    title: "Quarantine",
    confirm: "Quarantine",
    tone: "danger",
    body: "Every AI call, tool call and resource start by this person is refused until restored. Running resources keep running until stopped.",
  },
  revoke: {
    title: "Revoke access",
    confirm: "Revoke",
    tone: "danger",
    body: "The key stops authenticating entirely. Use for a stolen key or a departure.",
  },
  tighten: {
    title: "Tighten budget",
    confirm: "Apply budget",
    tone: "primary",
    body: "Scales this person's daily budgets. They keep working, with less headroom.",
  },
  restore: {
    title: "Restore access",
    confirm: "Restore",
    tone: "good",
    body: "Clears every restriction (status, budget scale, approvals) and resolves the open incidents behind it.",
  },
};

/** Quarantine / tighten / revoke / restore for one person, each behind a reason. */
export function RestrictActions({ pid, status, scale, compact }: { pid: string; status: string; scale: number; compact?: boolean }) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<Mode | null>(null);
  const [newScale, setNewScale] = useState(0.25);
  const restricted = status !== "active" || scale < 1;

  const run = async (reason: string) => {
    const body: Record<string, unknown> =
      mode === "restore"
        ? { clear: true, reason }
        : mode === "tighten"
          ? { budget_scale: newScale, reason }
          : { status: mode === "quarantine" ? "quarantined" : "revoked", reason };
    await admin.restrict(pid, body);
    await qc.invalidateQueries({ queryKey: ["admin"] });
  };

  const c = mode ? COPY[mode] : null;
  return (
    <>
      <div className="flex flex-wrap gap-2">
        {status !== "quarantined" && (
          <Button size={compact ? "sm" : "md"} variant="danger" onClick={() => setMode("quarantine")}>
            <IconLock size={14} /> Quarantine
          </Button>
        )}
        <Button size={compact ? "sm" : "md"} onClick={() => setMode("tighten")}>
          Tighten budget
        </Button>
        {status !== "revoked" && (
          <Button size={compact ? "sm" : "md"} onClick={() => setMode("revoke")}>
            Revoke key
          </Button>
        )}
        {restricted && (
          <Button size={compact ? "sm" : "md"} variant="good" onClick={() => setMode("restore")}>
            Restore
          </Button>
        )}
      </div>
      <ReasonDialog
        open={!!mode}
        onClose={() => setMode(null)}
        title={c ? `${c.title} · ${pid}` : ""}
        description={c?.body}
        confirmLabel={c?.confirm ?? ""}
        tone={c?.tone}
        placeholder={mode === "restore" ? "e.g. Investigated with the manager; legitimate migration work." : "e.g. Repeated exfiltration attempts, see incident a81511."}
        onConfirm={run}
      >
        {mode === "tighten" && (
          <Field label="Daily budget scale" hint={`Currently ${Math.round(scale * 100)}% of normal.`}>
            <div className="flex flex-wrap gap-1.5">
              {[0.1, 0.25, 0.5, 0.75].map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setNewScale(s)}
                  className={`rounded-md px-3 py-1.5 text-sm ring-1 ring-inset ${
                    newScale === s ? "bg-accent/15 text-accent ring-accent/40" : "text-ink2 ring-line hover:bg-raised"
                  }`}
                >
                  {Math.round(s * 100)}%
                </button>
              ))}
            </div>
          </Field>
        )}
      </ReasonDialog>
    </>
  );
}

/** "View events": content access needs a stated reason, which is logged and shown to the person. */
export function ViewEventsButton({ pid }: { pid: string }) {
  const qc = useQueryClient();
  const [ask, setAsk] = useState(false);
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [why, setWhy] = useState("");
  return (
    <>
      <Button onClick={() => setAsk(true)}>
        <IconEye size={14} /> View events
      </Button>
      <ReasonDialog
        open={ask}
        onClose={() => setAsk(false)}
        title={`View ${pid}'s events`}
        description={
          <div className="rounded-lg bg-cc/10 px-3 py-2 text-xs text-ink2 ring-1 ring-inset ring-cc/25">
            Event content (masked prompts and tool calls) is personal data. Your reason is written to the admin log and shown to{" "}
            <b>{pid}</b> on their privacy page.
          </div>
        }
        reasonLabel="Why do you need to see this?"
        reasonHint="Be specific: an incident id, a ticket, a request from the person."
        confirmLabel="Log reason and view"
        onConfirm={async (reason) => {
          const rows = await admin.personEvents(pid, reason, 200);
          setWhy(reason);
          setEvents(rows);
          await qc.invalidateQueries({ queryKey: ["admin", "person", pid] });
        }}
      />
      <Dialog
        open={events !== null}
        onClose={() => setEvents(null)}
        wide
        title={
          <span className="flex items-center gap-2">
            {pid}'s events <Pill tone="cc">access logged: “{why}”</Pill>
          </span>
        }
      >
        {events && events.length === 0 ? (
          <Empty title="No events in the audit buffer" />
        ) : (
          <TableWrap>
            <table className="tbl min-w-[720px]">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Channel</th>
                  <th>Decision</th>
                  <th>Content (masked)</th>
                  <th className="text-right">Cost</th>
                </tr>
              </thead>
              <tbody>
                {(events ?? []).map((e, i) => (
                  <tr key={`${e.request_id}-${e.direction}-${i}`}>
                    <td className="whitespace-nowrap text-xs text-muted">{dateTime(e.ts)}</td>
                    <td className="text-xs">
                      <div>
                        {e.channel} · {e.direction}
                      </div>
                      <div className="font-mono text-[11px] text-muted">{e.tool ?? e.model ?? ""}</div>
                    </td>
                    <td>
                      <DecisionPill decision={e.action} />
                    </td>
                    <td className="max-w-[380px] text-xs">
                      {e.text ? <div className="line-clamp-3 break-words font-mono text-[11px] text-ink2">{e.text}</div> : <span className="text-muted">not stored</span>}
                      {e.reason && <div className="mt-0.5 text-[11px] text-bad">{e.reason}</div>}
                    </td>
                    <td className="tnum text-right text-xs">{e.usd ? usd(e.usd) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Dialog>
    </>
  );
}
