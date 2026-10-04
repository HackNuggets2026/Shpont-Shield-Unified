// Attacks: the Redteam sidecar attacks the gateway with known attacks, their disguised rewrites and
// normal requests, and scores what got through. Settings are changed on the Controls page; this page
// shows what each change did to the score.
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ApiError, downloadExport } from "../../api";
import { redteam, type CaseRow, type PostureChange, type PostureReport, type RedteamStage, type RedteamState } from "../../redteamApi";
import { Button, Card, cx, Empty, ErrorBox, Loading, Mono, PageHeader, Pill, TONE_COLOR, type Tone } from "../../components/ui";
import { ago, pct, timeOfDay, titleCase } from "../../lib/format";

const TARGET = 90;
const CHANNEL: Record<string, string> = {
  input: "Prompt",
  tool_call: "Tool call",
  output: "Model reply",
  tool_result: "Tool data",
  tool_description: "Tool listing",
};

function scoreTone(v: number | null | undefined): Tone {
  if (v == null) return "neutral";
  return v >= TARGET ? "good" : v >= 70 ? "warn" : "bad";
}

/** The posture as a 270-degree ring. */
function ScoreRing({ value, size = 168 }: { value: number | null; size?: number }) {
  const r = 80;
  const c = 2 * Math.PI * r;
  const arc = c * 0.75;
  const filled = arc * Math.min(1, Math.max(0, (value ?? 0) / 100));
  const color = TONE_COLOR[scoreTone(value)];
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg viewBox="0 0 200 200" width={size} height={size} role="img" aria-label={`Posture ${value ?? "unknown"} of 100`}>
        <g transform="rotate(135 100 100)" fill="none" strokeWidth="14" strokeLinecap="round">
          <circle cx="100" cy="100" r={r} stroke="rgb(var(--ink) / 0.08)" strokeDasharray={`${arc} ${c}`} />
          <circle cx="100" cy="100" r={r} stroke={color} strokeDasharray={`${filled} ${c}`} style={{ transition: "stroke-dasharray 1s ease" }} />
        </g>
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="tnum text-4xl font-semibold tracking-tight text-ink">{value == null ? "n/a" : value.toFixed(1)}</span>
        <span className="mt-1 text-xs text-muted">out of 100</span>
      </div>
    </div>
  );
}

function changeTitle(c: PostureChange): string {
  if (c.controls?.length) return c.controls.map((k) => `${titleCase(k.id)} ${k.what}`).join(", ");
  return `${titleCase(c.cause.join(", ")) || "Settings"} changed`;
}

function PostureChart({ s }: { s: RedteamState }) {
  const changes = s.poligon.changes.filter((c) => c.changed);
  const rows = s.poligon.history.slice(-120).map((h) => ({ ts: h.ts, t: timeOfDay(h.ts), score: h.posture ?? 0, pin: "" as "" | "up" | "down", label: "" }));
  for (const c of changes) {
    const p = rows.find((h) => h.ts >= c.ts);
    if (p) {
      p.pin = (c.posture_after ?? 0) < (c.posture_before ?? 0) ? "down" : "up";
      p.label = changeTitle(c);
    }
  }
  if (rows.length < 2) return <Empty title="The line appears after the second run" hint="The sidecar re-runs every 30 s, and at once when the gateway's settings change." />;
  return (
    <ResponsiveContainer width="100%" height={210}>
      <AreaChart data={rows} margin={{ top: 10, right: 12, bottom: 0, left: -18 }}>
        <defs>
          <linearGradient id="posture-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="var(--s1)" stopOpacity={0.25} />
            <stop offset="1" stopColor="var(--s1)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} stroke="var(--grid)" />
        <XAxis dataKey="t" tick={{ fill: "var(--axis)", fontSize: 11 }} axisLine={{ stroke: "var(--grid)" }} tickLine={false} minTickGap={48} />
        <YAxis domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tick={{ fill: "var(--axis)", fontSize: 11 }} axisLine={false} tickLine={false} />
        <Tooltip
          content={({ active, payload }) => {
            const p = active && payload?.[0]?.payload;
            if (!p) return null;
            return (
              <div className="rounded-lg border border-line bg-panel px-3 py-2 text-xs shadow-xl">
                <div className="font-medium text-ink">
                  {p.t} · posture <span className="tnum">{p.score}</span>
                </div>
                {p.label && <div className={cx("mt-0.5", p.pin === "down" ? "text-bad" : "text-good")}>{p.label}</div>}
              </div>
            );
          }}
        />
        <ReferenceLine y={TARGET} stroke={TONE_COLOR.good} strokeDasharray="4 4" label={{ value: `target ${TARGET}`, position: "insideTopRight", fontSize: 11, fill: TONE_COLOR.good }} />
        <Area
          type="stepAfter"
          dataKey="score"
          stroke="var(--s1)"
          strokeWidth={2}
          fill="url(#posture-fill)"
          isAnimationActive={false}
          dot={({ cx: x, cy: y, payload, index }) =>
            payload.pin ? <circle key={index} cx={x} cy={y} r={5.5} fill={payload.pin === "down" ? TONE_COLOR.bad : TONE_COLOR.good} stroke="rgb(var(--panel))" strokeWidth={2} /> : <g key={index} />
          }
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function ChangeList({ changes }: { changes: PostureChange[] }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!changes.length)
    return (
      <Empty
        title="No settings change seen yet"
        hint={
          <>
            Change a guardrail on the <Link to="/console/controls" className="text-accent hover:underline">Controls</Link> page: the attacks re-run at once and the effect shows here.
          </>
        }
      />
    );
  return (
    <ul className="divide-y divide-line/60">
      {changes.slice(0, 6).map((c) => {
        const d = (c.posture_after ?? 0) - (c.posture_before ?? 0);
        const isOpen = open === c.ts;
        return (
          <li key={c.ts} className="py-2.5">
            <button type="button" className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 text-left text-sm" onClick={() => setOpen(isOpen ? null : c.ts)}>
              <Pill tone={d < 0 ? "bad" : d > 0 ? "good" : "neutral"}>{d > 0 ? `+${d.toFixed(1)}` : d.toFixed(1)}</Pill>
              <span className="min-w-0 flex-1 font-medium text-ink">{changeTitle(c)}</span>
              <span className="tnum text-ink2">
                {c.posture_before ?? "n/a"} → <b className="text-ink">{c.posture_after ?? "n/a"}</b>
              </span>
              <span className="w-16 text-right text-xs text-muted">{ago(c.ts)}</span>
            </button>
            <div className="mt-1 flex flex-wrap gap-1.5 pl-1 text-xs">
              {c.opened.length > 0 && <Pill tone="bad">{c.opened.length} attacks opened</Pill>}
              {c.closed.length > 0 && <Pill tone="good">{c.closed.length} attacks closed</Pill>}
              {c.new_false_positives.length > 0 && <Pill tone="warn">{c.new_false_positives.length} new false positives</Pill>}
              {c.fixed_false_positives.length > 0 && <Pill tone="info">{c.fixed_false_positives.length} false positives fixed</Pill>}
            </div>
            {isOpen && (
              <div className="mt-2 space-y-2 pl-1">
                {(
                  [
                    ["Started getting through", c.opened, "bad"],
                    ["Stopped again", c.closed, "good"],
                    ["Normal requests newly blocked", c.new_false_positives, "warn"],
                  ] as const
                ).map(([label, rows, tone]) =>
                  rows.length ? (
                    <div key={label}>
                      <div className="mb-1 text-xs font-medium text-ink2">
                        {label} ({rows.length})
                      </div>
                      <CaseList rows={rows.filter((o) => !o.technique).slice(0, 12)} tag={(o) => <Pill tone={tone}>{titleCase(o.category)}</Pill>} />
                    </div>
                  ) : null,
                )}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function CaseList({ rows, tag, empty }: { rows: CaseRow[]; tag: (o: CaseRow) => React.ReactNode; empty?: string }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!rows.length) return <Empty title={empty ?? "None"} />;
  return (
    <ul className="divide-y divide-line/60">
      {rows.map((o) => (
        <li key={o.id}>
          <button type="button" className="flex w-full items-center gap-3 py-2 text-left text-sm hover:bg-raised/60" onClick={() => setOpen(open === o.id ? null : o.id)}>
            <span className="w-36 shrink-0">{tag(o)}</span>
            <span className="hidden w-20 shrink-0 text-xs text-muted sm:inline">{CHANNEL[o.direction] ?? o.direction}</span>
            <span className={cx("min-w-0 flex-1 text-ink2", open === o.id ? "whitespace-pre-wrap break-words" : "truncate")}>{o.text}</span>
          </button>
          {open === o.id && (
            <div className="flex flex-wrap items-center gap-1.5 pb-2 pl-1 text-xs text-muted">
              <Mono>{o.id}</Mono>
              <span>sent by {o.principal}</span>
              <span>· gateway answered</span>
              <Pill tone={o.action === "block" || o.action === "redact" ? "good" : "bad"}>{o.action}</Pill>
              {o.caught_by.map((c) => (
                <Pill key={c}>{c}</Pill>
              ))}
              {o.owasp && <span>· OWASP {o.owasp}</span>}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

function Coverage({ stage }: { stage: RedteamStage }) {
  const cell: Record<string, [number, number]> = {};
  const base = stage.outcomes.filter((o) => o.attack && !o.rewrite);
  for (const o of base) {
    const c = (cell[`${o.category}|${o.direction}`] ??= [0, 0]);
    c[1]++;
    if (o.stopped) c[0]++;
  }
  const cats = [...new Set(base.map((o) => o.category))].sort();
  const dirs = Object.keys(CHANNEL).filter((d) => base.some((o) => o.direction === d));
  if (!cats.length) return <Empty title="No attacks fired yet" />;
  return (
    <div className="overflow-x-auto">
      <div className="grid min-w-[480px] gap-1.5 text-sm" style={{ gridTemplateColumns: `150px repeat(${dirs.length}, minmax(0,1fr))` }}>
        <span />
        {dirs.map((d) => (
          <span key={d} className="pb-1 text-center text-xs text-muted">
            {CHANNEL[d]}
          </span>
        ))}
        {cats.map((cat) => (
          <div key={cat} className="contents">
            <span className="self-center truncate pr-2 text-right text-ink2">{titleCase(cat)}</span>
            {dirs.map((d) => {
              const c = cell[`${cat}|${d}`];
              if (!c) return <span key={d} className="h-9 rounded-md border border-dashed border-line" title="No test for this combination" />;
              const f = c[0] / c[1];
              return (
                <span
                  key={d}
                  title={`${titleCase(cat)} via ${CHANNEL[d]}: ${c[0]} of ${c[1]} stopped`}
                  className={cx(
                    "tnum grid h-9 place-items-center rounded-md font-medium",
                    f === 1 ? "bg-good/15 text-good" : f >= 0.6 ? "bg-warn/15 text-warn" : "bg-bad/15 text-bad",
                  )}
                >
                  {c[0]}/{c[1]}
                </span>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

function Techniques({ r }: { r: PostureReport }) {
  if (!r.by_technique.length) return <Empty title="No rewrites fired" hint="Rewrites are made only from attacks whose original is stopped." />;
  return (
    <ul className="space-y-2">
      {r.by_technique.map((t) => {
        const f = t.total ? t.stopped / t.total : 0;
        const tone: Tone = f >= 0.9 ? "good" : f >= 0.6 ? "warn" : "bad";
        return (
          <li key={t.name} className="grid grid-cols-[7rem_minmax(0,1fr)_4.5rem] items-center gap-3 text-sm">
            <span className="truncate text-ink2">{titleCase(t.name)}</span>
            <span className="h-2 overflow-hidden rounded-full bg-ink/10">
              <span className="block h-full rounded-full" style={{ width: `${f * 100}%`, background: TONE_COLOR[tone] }} />
            </span>
            <span className="tnum text-right text-xs text-muted">
              {t.stopped}/{t.total}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function FeedHealth({ f }: { f: RedteamState["feedcheck"] }) {
  const broken = f.rows.filter((r) => !r.ok);
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone={f.error || f.broken ? "bad" : "good"} dot>
          {f.error ? "unavailable" : f.broken ? `${f.broken} broken` : "healthy"}
        </Pill>
        <Mono className="text-muted">{f.feed_version}</Mono>
      </div>
      <div className="grid grid-cols-3 gap-2 text-center">
        {(
          [
            ["Signatures", f.signatures],
            ["Broken", f.broken],
            ["Self-tested", f.self_tested],
          ] as const
        ).map(([k, v]) => (
          <div key={k} className="rounded-lg bg-ink/[0.04] py-2">
            <div className="tnum text-lg font-semibold text-ink">{v}</div>
            <div className="text-[11px] text-muted">{k}</div>
          </div>
        ))}
      </div>
      {f.error && <p className="text-xs text-bad">{f.error}</p>}
      {broken.map((b) => (
        <p key={b.id} className="text-xs text-bad">
          <Mono>{b.id}</Mono> {b.problems.join("; ")}
        </p>
      ))}
      <p className="text-xs text-muted">Every signature must compile, match its own examples and stay quiet on clean text.</p>
    </div>
  );
}

function SidecarDown({ error }: { error: unknown }) {
  const down = error instanceof ApiError && error.status === 503;
  if (!down) return <ErrorBox error={error} />;
  return (
    <Card>
      <Empty
        title="The attack sidecar is not running"
        hint="It is a separate process that attacks this gateway and scores what gets through. Start it next to the gateway, then reload this page."
      />
      <div className="mx-auto mb-6 max-w-xl space-y-2 rounded-xl bg-ink/[0.04] p-4 font-mono text-[12px] text-ink2">
        <div>make redteam</div>
        <div className="text-muted"># or</div>
        <div>cd redteam && SHIELD_URL=http://127.0.0.1:8787 ../.venv/bin/python -m shield_redteam serve</div>
        <div className="text-muted"># the gateway looks for it at ACL_REDTEAM_URL (default http://127.0.0.1:8799)</div>
      </div>
    </Card>
  );
}

export function AttacksPage() {
  const qc = useQueryClient();
  const state = useQuery({ queryKey: ["redteam", "state"], queryFn: redteam.state, refetchInterval: 5_000, retry: false });
  const stage = useQuery({ queryKey: ["redteam", "stage"], queryFn: redteam.stage, refetchInterval: 5_000, retry: false, enabled: state.isSuccess });
  const run = useMutation({ mutationFn: redteam.run, onSettled: () => qc.invalidateQueries({ queryKey: ["redteam"] }) });
  const s = state.data;
  const r = s?.poligon.report;

  const actions = (
    <>
      <Button size="sm" variant="ghost" disabled={!r} onClick={() => downloadExport(redteam.reportPath, "posture-report.md")}>
        Report
      </Button>
      <Button size="sm" variant="primary" disabled={!s || run.isPending} onClick={() => run.mutate()}>
        {run.isPending ? "Running…" : "Run now"}
      </Button>
    </>
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Attacks"
        subtitle="A sidecar attacks this gateway around the clock with known attacks, disguised rewrites of them and normal work requests. The score is measured, not estimated: 100 × attacks stopped × (1 − normal requests blocked)."
        actions={actions}
      />
      {run.isError && <ErrorBox error={run.error} compact />}

      {state.isPending ? (
        <Loading rows={6} />
      ) : state.isError ? (
        <SidecarDown error={state.error} />
      ) : !r || !s ? (
        <Card>
          <Empty
            title={s?.poligon.error ? "The sidecar cannot reach the gateway" : "The first run has not finished yet"}
            hint={s?.poligon.error || `${s?.poligon.corpus.cases ?? 0} test messages and ${s?.poligon.corpus.mutants ?? 0} rewrites are queued.`}
          />
        </Card>
      ) : (
        <>
          {s.poligon.error && <ErrorBox error={s.poligon.error} compact />}
          <div className="grid gap-4 lg:grid-cols-3">
            <Card title="Posture" subtitle={`Last run ${ago(r.started)}, ${r.cases} messages in ${r.seconds} s`} tint={TONE_COLOR[scoreTone(r.posture)]}>
              <div className="flex flex-col items-center">
                <ScoreRing value={r.posture} />
                <p className="mt-1 text-center text-sm text-ink2">
                  {r.attacks.total - r.attacks.stopped ? (
                    <>
                      <b className="text-ink">{r.attacks.total - r.attacks.stopped}</b> of {r.attacks.total} known attacks get through.
                    </>
                  ) : (
                    <>All {r.attacks.total} known attacks are stopped.</>
                  )}
                </p>
                <div className="mt-3 grid w-full grid-cols-3 gap-2 text-center text-xs">
                  <div>
                    <div className="tnum text-sm font-semibold text-ink">{pct(r.protection)}</div>
                    <div className="text-muted">attacks stopped</div>
                  </div>
                  <div>
                    <div className="tnum text-sm font-semibold text-ink">{pct(r.friction)}</div>
                    <div className="text-muted">normal blocked</div>
                  </div>
                  <div>
                    <div className="tnum text-sm font-semibold text-ink">{pct(r.evasion)}</div>
                    <div className="text-muted">rewrites stopped</div>
                  </div>
                </div>
              </div>
            </Card>
            <Card className="lg:col-span-2" title="Posture over time" subtitle={`Score after every run, target ${TARGET}. A dot marks a change in the gateway's settings.`}>
              <PostureChart s={s} />
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2" title="What the last settings changes did" subtitle="Posture before and after, attacks opened or closed, normal requests newly blocked. Open one for the cases.">
              <ChangeList changes={s.poligon.changes.filter((c) => c.changed)} />
            </Card>
            <Card title="Feed health" subtitle="Self-test of the attack signature feed the gateway enforces">
              <FeedHealth f={s.feedcheck} />
            </Card>
          </div>

          <Card title="Where are the holes?" subtitle="Attack type by the channel it arrives on. Each cell: stopped of sent.">
            {stage.data ? <Coverage stage={stage.data} /> : stage.isError ? <ErrorBox error={stage.error} compact /> : <Loading rows={4} />}
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title={`Attacks that get through (${r.open_attacks.length})`} subtitle="The to-do list for whoever maintains the guardrails" tint={r.open_attacks.length ? TONE_COLOR.bad : undefined}>
              <CaseList rows={r.open_attacks} empty="Every known attack is stopped." tag={(o) => <Pill tone="bad">{titleCase(o.category)}</Pill>} />
            </Card>
            <Card title={`Normal requests blocked (${r.false_positives.length})`} subtitle="What the guardrails cost people doing their job" tint={r.false_positives.length ? TONE_COLOR.warn : undefined}>
              <CaseList rows={r.false_positives} empty="No normal request is blocked." tag={(o) => <Pill tone="warn">{o.caught_by.map((x) => titleCase(x.split("/")[0])).join(", ") || o.action}</Pill>} />
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Card title="Rewrites still stopped, by technique" subtitle="The same attacks disguised to slip past a filter">
              <Techniques r={r} />
            </Card>
            <Card className="lg:col-span-2" title={`Disguised attacks that get through (${r.evaded.length})`} subtitle="The original is stopped, the rewritten copy is not">
              <CaseList rows={r.evaded.slice(0, 40)} empty="Every rewrite is stopped." tag={(o) => <Pill>{titleCase(o.technique ?? "")}</Pill>} />
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
