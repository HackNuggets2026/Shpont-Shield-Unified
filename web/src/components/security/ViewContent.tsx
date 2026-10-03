import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { admin, type AuditEvent } from "../../api";
import { ago, dateTime } from "../../lib/format";
import { Dialog } from "../Dialog";
import { DecisionPill } from "../pills";
import { Button, Empty, ErrorBox, Field, Pill, Segmented, cx } from "../ui";
import { IconEye } from "../icons";

/**
 * "View content": masked prompts and tool calls are personal data, so the server requires a stated reason,
 * logs it and shows it to the employee. Rows the incident names are highlighted.
 */
export function ViewContentButton({
  pid,
  incidentId,
  evidence,
  window,
  suggestion,
}: {
  pid: string;
  incidentId: string;
  evidence: string[];
  window: [number, number];
  suggestion: string;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [rows, setRows] = useState<AuditEvent[] | null>(null);
  const [logged, setLogged] = useState("");
  const [scope, setScope] = useState<"window" | "all">("window");

  useEffect(() => {
    if (open) {
      setReason("");
      setError(null);
      setRows(null);
      setScope("window");
    }
  }, [open]);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      // Sent as typed (trimmed): an empty reason reaches the server, which refuses it with its own message.
      const r = await admin.personEvents(pid, reason.trim(), 500);
      setRows(r);
      setLogged(reason.trim());
      await qc.invalidateQueries({ queryKey: ["admin"] });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const ev = new Set(evidence);
  const shown = (rows ?? []).filter((r) => scope === "all" || ev.has(r.request_id) || (r.ts >= window[0] && r.ts <= window[1]));

  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>
        <IconEye size={14} /> View content
      </Button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        wide={rows !== null}
        title={
          rows === null ? (
            `View ${pid}'s content`
          ) : (
            <span className="flex flex-wrap items-center gap-2">
              {pid}'s content <Pill tone="cc">access logged: “{logged}”</Pill>
            </span>
          )
        }
        footer={
          rows === null ? (
            <>
              <Button variant="ghost" onClick={() => setOpen(false)} disabled={busy}>
                Cancel
              </Button>
              <Button variant="primary" onClick={submit} disabled={busy}>
                {busy ? "Working…" : "Log reason and view"}
              </Button>
            </>
          ) : (
            <Button onClick={() => setOpen(false)}>Close</Button>
          )
        }
      >
        {rows === null ? (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            <div className="rounded-lg bg-cc/10 px-3 py-2 text-xs text-ink2 ring-1 ring-inset ring-cc/25">
              Prompts and tool calls are personal data. Your reason goes to the admin log and is shown to <b>{pid}</b> on their privacy page.
            </div>
            <Field label="Why do you need to see this?" hint="The server refuses the request without a reason.">
              <textarea
                className="input min-h-[76px] resize-y"
                value={reason}
                placeholder={suggestion}
                onChange={(e) => setReason(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void submit();
                }}
              />
            </Field>
            {!reason && (
              <button type="button" onClick={() => setReason(suggestion)} className="text-xs font-medium text-accent hover:underline">
                Use “{suggestion}”
              </button>
            )}
            {error != null && <ErrorBox error={error} compact />}
          </form>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-muted">
                {rows.length} event{rows.length === 1 ? "" : "s"} in the gateway's content buffer · evidence for {incidentId} highlighted
              </p>
              <Segmented
                value={scope}
                onChange={setScope}
                options={[
                  { value: "window", label: "Around the incident" },
                  { value: "all", label: "Everything" },
                ]}
              />
            </div>
            {shown.length === 0 ? (
              <Empty
                title={rows.length ? "Nothing around the incident" : "No content held for this person"}
                hint={
                  rows.length
                    ? "Switch to Everything to see the rest of the buffer."
                    : "Masked content is kept in memory only for traffic since the gateway started; seeded history has none. The evidence timeline still shows what each blocked request was about."
                }
              />
            ) : (
              <ul className="divide-y divide-line/60 rounded-lg ring-1 ring-line">
                {shown.map((r, i) => (
                  <li key={`${r.request_id}-${r.direction}-${i}`} className={cx("px-3 py-2.5 text-xs", ev.has(r.request_id) && "bg-bad/[0.08]")}>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="tnum text-muted" title={dateTime(r.ts)}>
                        {dateTime(r.ts)} · {ago(r.ts)}
                      </span>
                      <span className="text-ink2">
                        {r.channel} · {r.direction}
                      </span>
                      {(r.tool || r.model) && <span className="font-mono text-[11px] text-muted">{r.tool ?? r.model}</span>}
                      <DecisionPill decision={r.action} />
                      {ev.has(r.request_id) && (
                        <Pill tone="bad" className="uppercase tracking-wide">
                          evidence
                        </Pill>
                      )}
                    </div>
                    {r.text ? (
                      <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-raised px-2 py-1.5 font-mono text-[11px] text-ink">{r.text}</pre>
                    ) : (
                      <div className="mt-1 text-muted">content not stored</div>
                    )}
                    {r.reason && <div className="mt-1 text-[11px] text-bad">{r.reason}</div>}
                    {r.findings.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {r.findings.map((f, j) => (
                          <span key={j} className="rounded bg-ink/[0.05] px-1.5 py-px font-mono text-[10.5px] text-ink2" title={f.detail}>
                            {f.control}/{f.category}:{f.action}
                          </span>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Dialog>
    </>
  );
}
