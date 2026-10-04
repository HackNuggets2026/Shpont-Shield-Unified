import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { admin, type PersonRisk, type RiskLevel } from "../api";
import { ago, dateTime } from "../lib/format";
import { trustOf } from "../lib/security";
import { Sparkline } from "./charts";
import { ReasonDialog } from "./Dialog";
import { RestrictActions } from "./RestrictActions";
import { Button, Card, ErrorBox, Loading, Pill, TONE_COLOR, cx } from "./ui";
import { SeverityPill } from "./pills";

type Choice = "auto" | "watch" | "restricted";

const CHOICES: { value: Choice; label: string; hint: string }[] = [
  { value: "auto", label: "Auto", hint: "The score decides" },
  { value: "watch", label: "Watch", hint: "Stricter checks, full text kept" },
  { value: "restricted", label: "Restricted", hint: "Every request blocked" },
];

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** "3 blocks and 1 trap opened in the last 24 h; the score halves every day." */
function why(r: PersonRisk): string {
  const { blocks, incidents, traps } = r.last_24h;
  const other = incidents - traps;
  const parts = [
    blocks && plural(blocks, "block"),
    other && plural(other, "incident"),
    traps && `${plural(traps, "trap")} opened`,
  ].filter(Boolean) as string[];
  const hl = r.half_life_hours === 24 ? "every day" : `every ${r.half_life_hours} h`;
  const list = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0];
  if (list) return `${list} in the last 24 h; the score halves ${hl}.`;
  if (r.score >= 1) return `Nothing new in the last 24 h; older events still count and halve ${hl}.`;
  return "No blocks, incidents or signals counting against this person.";
}

function TrustBar({ trust, watchAt }: { trust: number; watchAt: number }) {
  const tone = trust <= 0 ? "bg-bad" : trust <= watchAt ? "bg-warn" : "bg-good";
  return (
    <div>
      <div className="relative h-2.5 rounded-full bg-ink/10">
        <div className={cx("h-full rounded-full transition-[width]", tone)} style={{ width: `${Math.max(trust, 1.5)}%` }} />
        <span className="absolute -top-1 h-[calc(100%+8px)] w-0.5 rounded bg-warn" style={{ left: `${watchAt}%` }} title={`Watch below ${watchAt}`} />
        <span className="absolute -top-1 left-0 h-[calc(100%+8px)] w-0.5 rounded bg-bad" title="Restricted at 0" />
      </div>
      <div className="relative mt-1 h-3.5 text-[10px] text-muted">
        <span className="absolute left-0">Restricted at 0</span>
        <span className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: `${watchAt}%` }}>
          Watch below {watchAt}
        </span>
        <span className="absolute right-0">100</span>
      </div>
    </div>
  );
}

/** The Person page's insider-risk card: who decides the level, the trust score, why, and what feeds it. */
export function InsiderRiskCard({ pid, status, scale }: { pid: string; status: string; scale: number }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin", "risk", pid], queryFn: () => admin.risk(pid), refetchInterval: 10_000 });
  const [asking, setAsking] = useState<Choice | "normal" | null>(null);

  const done = async () => {
    await qc.invalidateQueries({ queryKey: ["admin"] });
  };

  if (q.isPending)
    return (
      <Card title="Insider risk">
        <Loading rows={4} />
      </Card>
    );
  if (q.isError)
    return (
      <Card title="Insider risk">
        <ErrorBox error={q.error} retry={() => q.refetch()} compact />
      </Card>
    );
  const r = q.data;
  const held = r.manual && r.manual.level !== "normal" ? (r.manual.level as Exclude<RiskLevel, "normal">) : null;
  const choice: Choice = held ?? "auto";
  const cleared = r.manual?.level === "normal";
  const trust = trustOf(r.score, r.levels.restricted);
  const watchAt = trustOf(r.levels.watch, r.levels.restricted);
  const raisedBy =
    !held && r.level !== r.computed
      ? r.signals.some((s) => s.level === r.level)
        ? "an external signal"
        : "their owner's level"
      : null;
  const recent = [
    ...r.alerts.map((a) => ({ ts: a.ts, kind: "alert" as const, text: a.reason, sev: null as string | null })),
    ...r.incidents.map((i) => ({ ts: i.ts, kind: "incident" as const, text: `${i.rule.replace(/_/g, " ")}: ${i.detail}`, sev: i.severity })),
  ]
    .sort((a, b) => b.ts - a.ts)
    .slice(0, 6);

  const pick = (c: Choice) => {
    if (c === choice && !(c === "auto" && cleared)) return;
    setAsking(c);
  };

  const levelNow =
    r.level === "restricted"
      ? "Every request by this person and their agents is blocked."
      : r.level === "watch"
        ? "Stricter checks apply and the full text of their requests is kept."
        : "No extra checks.";

  return (
    <Card title="Insider risk" subtitle="Every change is logged with your reason.">
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          <div>
            <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">Level</div>
            <div className="inline-flex rounded-lg bg-raised p-0.5 ring-1 ring-inset ring-line/70" role="radiogroup" aria-label="Insider-risk level">
              {CHOICES.map((c) => {
                const on = c.value === choice;
                const tone = c.value === "restricted" ? "bg-bad text-white" : c.value === "watch" ? "bg-warn text-ink" : "bg-ink text-page";
                return (
                  <button
                    key={c.value}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    title={c.hint}
                    onClick={() => pick(c.value)}
                    className={cx("h-8 rounded-md px-4 text-sm font-medium transition-colors", on ? `${tone} shadow-sm` : "text-muted hover:text-ink")}
                  >
                    {c.value === "auto" && cleared ? "Auto (cleared)" : c.label}
                  </button>
                );
              })}
            </div>
            <div className="mt-2 text-sm text-ink2">
              {held ? (
                <>
                  Set by {r.manual?.by || "security"}
                  {r.manual?.at ? <span title={dateTime(r.manual.at)}> {ago(r.manual.at)}</span> : null}
                  {r.manual?.reason ? <>: “{r.manual.reason}”</> : null}. {levelNow}
                </>
              ) : (
                <>
                  {cleared ? "An earlier override cleared this person; the score is ignored until set back to Auto. " : ""}
                  {r.level === "normal" ? "The score is below the Watch line." : `The score puts them at ${r.level === "restricted" ? "Restricted" : "Watch"}`}
                  {raisedBy && r.level !== "normal" ? `, raised by ${raisedBy}` : ""}
                  {r.level === "normal" ? "" : "."} {r.level === "normal" ? "" : levelNow}
                </>
              )}
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            {(r.score >= 1 || r.manual) && (
              <Button size="sm" variant="good" onClick={() => setAsking("normal")}>
                Back to normal
              </Button>
            )}
            <RestrictActions
              pid={pid}
              status={status}
              scale={scale}
              level={r.level}
              compact
              only={r.level === "watch" ? ["tighten", "revoke", "restore"] : ["revoke", "restore"]}
            />
          </div>

          {r.signals.length > 0 && (
            <div>
              <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">External signals</div>
              <ul className="divide-y divide-line/60 rounded-lg ring-1 ring-inset ring-line/70">
                {r.signals.map((s) => (
                  <li key={s.source} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono text-xs text-ink">{s.source}</span>
                        <Pill tone={s.level === "restricted" ? "bad" : "warn"}>{s.level === "restricted" ? "Restricted" : "Watch"}</Pill>
                      </div>
                      <div className="truncate text-xs text-muted">
                        {s.reason || "no reason given"} · expires {ago(s.expires_at)}
                      </div>
                    </div>
                    <Button
                      size="sm"
                      onClick={async () => {
                        await admin.dismissSignal(pid, s.source);
                        await done();
                      }}
                    >
                      Dismiss
                    </Button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <div className="space-y-4">
          <div>
            <div className="flex items-baseline justify-between">
              <div className="text-[11px] font-medium uppercase tracking-wide text-muted">Trust score</div>
              <div className="text-xs text-muted">risk score {Math.round(r.score)}</div>
            </div>
            <div className="mb-2 flex items-baseline gap-2">
              <span className={cx("tnum text-3xl font-semibold tracking-tight", trust <= 0 ? "text-bad" : trust <= watchAt ? "text-warn" : "text-ink")}>{trust}</span>
              <span className="text-sm text-muted">/ 100</span>
            </div>
            <TrustBar trust={trust} watchAt={watchAt} />
            <p className="mt-2 text-sm text-ink2">{why(r)}</p>
          </div>
          <div>
            <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">Trust score, last 7 days</div>
            <Sparkline values={r.history.map((h) => trustOf(h.score, r.levels.restricted))} min={0} height={40} color={trust <= 0 ? TONE_COLOR.bad : trust <= watchAt ? TONE_COLOR.warn : TONE_COLOR.good} />
          </div>
          <div>
            <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">Recent alerts and incidents</div>
            {recent.length === 0 ? (
              <div className="text-sm text-muted">None in the last 7 days.</div>
            ) : (
              <ul className="space-y-1.5">
                {recent.map((x, i) => (
                  <li key={i} className="flex items-baseline gap-2 text-sm">
                    {x.kind === "incident" ? <SeverityPill severity={x.sev} /> : <Pill tone="warn">alert</Pill>}
                    <span className="min-w-0 flex-1 truncate text-ink2" title={x.text}>
                      {x.text}
                    </span>
                    <span className="shrink-0 text-xs text-muted" title={dateTime(x.ts)}>
                      {ago(x.ts)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>

      <ReasonDialog
        open={!!asking}
        onClose={() => setAsking(null)}
        title={
          asking === "normal"
            ? `Back to normal · ${pid}`
            : asking === "auto"
              ? `Back to Auto · ${pid}`
              : asking === "watch"
                ? `Watch · ${pid}`
                : `Restrict · ${pid}`
        }
        description={
          asking === "normal"
            ? "Sets the level to Auto and resets the score to 0. Incidents stay on file."
            : asking === "auto"
              ? "The score decides the level again."
              : asking === "watch"
                ? "Holds this person at Watch whatever the score: stricter checks and full text kept."
                : "Blocks every request by this person and their agents until set back to Auto."
        }
        confirmLabel={asking === "restricted" ? "Restrict" : asking === "watch" ? "Watch" : asking === "normal" ? "Back to normal" : "Set to Auto"}
        tone={asking === "restricted" ? "danger" : asking === "normal" ? "good" : "primary"}
        reasonRequired={asking === "watch" || asking === "restricted"}
        reasonHint="Logged with your name in the audit log. The person is not told their level."
        placeholder={asking === "restricted" ? "e.g. Opened the board pack trap, see the incident." : "e.g. Reviewed with the manager; legitimate work."}
        onConfirm={async (reason) => {
          if (!asking) return;
          if (asking === "normal") await admin.setRisk(pid, { level: "auto", reason, reset_score: true });
          else await admin.setRisk(pid, { level: asking, reason });
          await done();
        }}
      />
    </Card>
  );
}
