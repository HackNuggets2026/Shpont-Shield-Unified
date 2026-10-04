// Controls: every guardrail in one editable table (switch, mode, shadow, threshold), the engine's status
// in one line, and the overhead each stage adds. Edits go through the admin overlay: validated, live at
// once, kept across restarts.
import { Fragment, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { controlsApi, type ControlMode, type ControlProfile, type ControlRow, type ControlsSummary } from "../../api";
import { Card, Dot, Empty, Mono, PageHeader, Pill, Q, Segmented, TableWrap, Toggle, cx, type Tone } from "../../components/ui";
import { IconAlert, IconChevronRight, IconX } from "../../components/icons";

const MODES: ControlMode[] = ["allow", "log", "warn", "redact", "block"];
const MODE_TONE: Record<ControlMode, Tone> = { allow: "neutral", log: "info", warn: "warn", redact: "serious", block: "bad" };
const KIND_ORDER = { deterministic: 0, context: 1, semantic: 2 } as const;
const KIND_LABEL = { deterministic: "pattern", context: "context model", semantic: "semantic" } as const;
const TITLES: Record<string, string> = {
  secrets: "Secrets",
  pii: "Personal data",
  pii_model: "Personal data (context)",
  signatures: "Known exploits",
  tool_access: "Tool permissions",
};
const BLURB: Record<string, string> = {
  secrets: "API keys, tokens, private keys and passwords, by exact pattern.",
  pii: "Card numbers, IBANs, national IDs, emails and phones, by exact pattern.",
  signatures: "Known attack strings from the signature feed.",
  tool_access: "Which tools each role may call; irreversible tools need approval.",
};
const PROFILE_HINT: Record<ControlProfile, string> = {
  strict: "Every control on and enforcing, personal data blocked, model thresholds 0.15 lower",
  balanced: "The values in policy.yaml",
  permissive: "Personal data only logged, model thresholds 0.1 higher",
};

const title = (n: string) => TITLES[n] ?? n.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

export function ControlsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin", "controls", "summary"], queryFn: controlsApi.summary, refetchInterval: 10_000 });
  const prof = useQuery({ queryKey: ["admin", "controls", "profile"], queryFn: controlsApi.profile, refetchInterval: 30_000 });
  const [error, setError] = useState<string | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["admin", "controls"] });
  const onError = (e: unknown) => {
    setError(e instanceof Error ? e.message : String(e));
    void refresh(); // the table snaps back to what the gateway still enforces
  };
  const edit = useMutation({
    mutationFn: ({ name, body }: { name: string; body: Parameters<typeof controlsApi.edit>[1] }) => controlsApi.edit(name, body),
    onMutate: () => setError(null),
    onSuccess: refresh,
    onError,
  });
  const profile = useMutation({
    mutationFn: controlsApi.setProfile,
    onMutate: () => setError(null),
    onSuccess: refresh,
    onError,
  });
  const active = prof.data?.active ?? null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Controls"
        subtitle="Every guardrail the gateway runs on each request. Changes apply at once and survive a restart; a value the policy cannot load is refused."
        actions={
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted">Strictness</span>
            <Segmented<ControlProfile>
              value={(active ?? "custom") as ControlProfile}
              onChange={(p) => profile.mutate(p)}
              options={(["strict", "balanced", "permissive"] as const).map((p) => ({
                value: p,
                label: <span title={PROFILE_HINT[p]}>{p[0].toUpperCase() + p.slice(1)}</span>,
              }))}
            />
            {prof.data && !active && <Pill title="Controls were edited one by one since the last profile">custom</Pill>}
          </div>
        }
      />

      <Q q={q} rows={6}>
        {(s) => (
          <>
            {s.policy.last_error && (
              <div role="alert" className="flex items-start gap-2 rounded-xl bg-bad/10 px-4 py-3 text-sm text-bad ring-1 ring-inset ring-bad/30">
                <IconAlert className="mt-0.5 shrink-0" />
                <div className="min-w-0">
                  <b>The last policy edit was rejected</b>; the gateway keeps enforcing version <Mono>{s.policy.version}</Mono>.
                  <div className="mt-1 break-words font-mono text-xs">{s.policy.last_error}</div>
                </div>
              </div>
            )}
            <StatusStrip s={s} />
            {error && (
              <div role="alert" className="flex items-start justify-between gap-3 rounded-xl bg-bad/10 px-4 py-2.5 text-sm text-bad ring-1 ring-inset ring-bad/30">
                <span className="min-w-0 break-words">Not saved: {error}</span>
                <button type="button" onClick={() => setError(null)} aria-label="Dismiss" className="shrink-0">
                  <IconX />
                </button>
              </div>
            )}
            <div className="grid gap-4 xl:grid-cols-3">
              <Card
                className="xl:col-span-2"
                flush
                title="Guardrails"
                subtitle="Mode is the strongest action a control may take. Shadow logs what it would do and changes nothing. Open a row for what it caught."
              >
                <ControlTable s={s} busy={edit.isPending || profile.isPending} edit={(name, body) => edit.mutate({ name, body })} />
              </Card>
              <Latency s={s} />
            </div>
          </>
        )}
      </Q>
    </div>
  );
}

function StatusStrip({ s }: { s: ControlsSummary }) {
  const sem = s.semantic;
  const engine =
    sem.backend === "heuristic" ? (
      <>
        keyword fallback, <b>no model</b>
      </>
    ) : sem.backend === "off" ? (
      <b>off</b>
    ) : (
      <>
        Ollama <Mono>{sem.fast_model}</Mono>
        {sem.deep_model && (
          <>
            {" "}then <Mono>{sem.deep_model}</Mono>
          </>
        )}
      </>
    );
  const item = (label: string, body: ReactNode) => (
    <span className="inline-flex min-w-0 flex-wrap items-center gap-1.5">
      <span className="text-muted">{label}</span>
      {body}
    </span>
  );
  return (
    <div className="soft-card flex flex-wrap items-center gap-x-6 gap-y-2 rounded-2xl px-5 py-3 text-sm text-ink2">
      {item(
        "Policy",
        <>
          <b className="text-ink">{s.policy.name}</b> <Mono>{s.policy.version}</Mono>
          <span className="text-muted">
            · {s.policy.reloads} reload{s.policy.reloads === 1 ? "" : "s"}
          </span>
        </>,
      )}
      {item(
        "Signature feed",
        <>
          <Mono>{s.feed.version ?? "none"}</Mono>
          <span className="text-muted">· {s.feed.signatures} signatures</span>
          {s.feed.errors.length > 0 && (
            <Pill tone="bad" title={s.feed.errors.join("; ")}>
              {s.feed.errors.length} error{s.feed.errors.length === 1 ? "" : "s"}
            </Pill>
          )}
        </>,
      )}
      {item(
        "Semantic checks",
        <>
          {engine}
          <Pill tone={sem.fail_mode === "closed" ? "good" : "warn"} title="What happens when the model is down or slow">
            fails {sem.fail_mode}
          </Pill>
        </>,
      )}
    </div>
  );
}

type Edit = (name: string, body: Parameters<typeof controlsApi.edit>[1]) => void;

function ControlTable({ s, edit, busy }: { s: ControlsSummary; edit: Edit; busy: boolean }) {
  const [open, setOpen] = useState<string | null>(null);
  const rows = [...s.controls].sort((a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || b.hits - a.hits);
  const would: Record<string, string[]> = {};
  for (const [k, n] of s.shadow_would_have) {
    const i = k.lastIndexOf(":");
    (would[k.slice(0, i)] ??= []).push(`${k.slice(i + 1)} ×${n}`);
  }
  if (!rows.length) return <Empty title="No controls in the policy" />;
  return (
    <TableWrap>
      <table className="tbl min-w-[760px]">
        <thead>
          <tr>
            <th>Control</th>
            <th>On</th>
            <th>Mode</th>
            <th>Shadow</th>
            <th>Threshold</th>
            <th className="text-right">Hits</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => {
            const isOpen = open === c.name;
            return (
              <Fragment key={c.name}>
                <tr className={cx(!c.enabled && "opacity-60")}>
                  <td className="w-[300px]">
                    <button
                      type="button"
                      onClick={() => setOpen(isOpen ? null : c.name)}
                      aria-expanded={isOpen}
                      className="group flex min-w-0 items-start gap-1.5 text-left"
                    >
                      <IconChevronRight className={cx("mt-0.5 shrink-0 text-muted transition-transform", isOpen && "rotate-90")} />
                      <span className="min-w-0">
                        <span className="font-medium text-ink group-hover:underline">{title(c.name)}</span>{" "}
                        <span className="text-xs text-muted">{KIND_LABEL[c.kind]}</span>
                        {(c.description || BLURB[c.name]) && (
                          <span className="block max-w-[260px] truncate text-xs text-muted" title={c.description ?? BLURB[c.name]}>{c.description ?? BLURB[c.name]}</span>
                        )}
                      </span>
                    </button>
                  </td>
                  <td>
                    <Toggle checked={c.enabled} disabled={busy} label={`${title(c.name)} on or off`} onChange={(v) => edit(c.name, { enabled: v })} />
                  </td>
                  <td>
                    <ModeSelect c={c} disabled={busy || !c.enabled} onChange={(m) => edit(c.name, { mode: m })} />
                  </td>
                  <td>
                    <Toggle checked={c.shadow} disabled={busy || !c.enabled} label={`${title(c.name)} shadow`} onChange={(v) => edit(c.name, { shadow: v })} />
                  </td>
                  <td>
                    {c.threshold ? (
                      <ThresholdSlider key={`${c.name}:${c.threshold.action}:${c.threshold.value}`} c={c} disabled={busy || !c.enabled} onCommit={(v) => edit(c.name, { threshold: v })} />
                    ) : (
                      <span className="text-xs text-muted">exact match</span>
                    )}
                  </td>
                  <td className="text-right tabular-nums">
                    {c.hits ? (
                      <Link to={`/console/activity?control=${encodeURIComponent(c.name)}`} className="font-medium text-accent hover:underline">
                        {c.hits.toLocaleString()}
                      </Link>
                    ) : (
                      <span className="text-muted">0</span>
                    )}
                  </td>
                </tr>
                {isOpen && (
                  <tr>
                    <td colSpan={6} className="bg-raised/40">
                      <Categories c={c} would={would} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </TableWrap>
  );
}

function ModeSelect({ c, disabled, onChange }: { c: ControlRow; disabled: boolean; onChange: (m: ControlMode) => void }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <select
        value={c.mode}
        disabled={disabled}
        aria-label={`${title(c.name)} mode`}
        onChange={(e) => onChange(e.target.value as ControlMode)}
        className="h-7 rounded-md border border-line bg-panel px-2 text-xs text-ink focus:border-accent focus:outline-none disabled:opacity-60"
      >
        {MODES.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
      <Dot tone={MODE_TONE[c.mode]} />
    </span>
  );
}

function ThresholdSlider({ c, disabled, onCommit }: { c: ControlRow; disabled: boolean; onCommit: (v: number) => void }) {
  const th = c.threshold!;
  const [v, setV] = useState(th.value);
  const commit = () => {
    if (v !== th.value) onCommit(v);
  };
  const label = th.action === "min_score" ? "min score" : `${th.action} at`;
  return (
    <label className="flex items-center gap-2" title={th.action === "min_score" ? "Spans scored below this are ignored" : `Probability at which it ${th.action}s`}>
      <span className="w-16 shrink-0 text-[11px] text-muted">{label}</span>
      <input
        type="range"
        min={0.05}
        max={0.99}
        step={0.01}
        value={v}
        disabled={disabled}
        aria-label={`${title(c.name)} threshold`}
        onChange={(e) => setV(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
        className="w-24 accent-[rgb(var(--accent))] disabled:opacity-60"
      />
      <span className="w-8 font-mono text-xs tabular-nums text-ink">{v.toFixed(2)}</span>
    </label>
  );
}

function Categories({ c, would }: { c: ControlRow; would: Record<string, string[]> }) {
  const max = Math.max(1, ...c.categories.map(([, n]) => n));
  return (
    <div className="space-y-2 py-1 pl-6">
      {c.description && <p className="max-w-3xl text-xs text-ink2">{c.description}</p>}
      {c.categories.length === 0 ? (
        <p className="text-xs text-muted">Nothing caught since the gateway started.</p>
      ) : (
        <ul className="grid max-w-2xl gap-1.5">
          {c.categories.map(([cat, n]) => {
            const w = would[`${c.name}/${cat}`];
            return (
              <li key={cat} className="grid grid-cols-[minmax(0,12rem)_minmax(0,1fr)_auto] items-center gap-3 text-xs">
                <Mono className="truncate">{cat}</Mono>
                <span className="h-1.5 overflow-hidden rounded-full bg-ink/[0.06]">
                  <span className="block h-full rounded-full bg-accent/70" style={{ width: `${(100 * n) / max}%` }} />
                </span>
                <span className="flex items-center gap-2 tabular-nums">
                  {n}
                  {w && (
                    <Pill tone="warn" title="Held back by shadow or a lower mode; this is what the control proposed">
                      would {w.join(", ")}
                    </Pill>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function Latency({ s }: { s: ControlsSummary }) {
  const total = s.latency_ms.total;
  const stages = Object.entries(s.latency_ms)
    .filter(([k]) => k !== "total")
    .sort((a, b) => b[1].p95 - a[1].p95);
  const max = Math.max(0.001, ...stages.map(([, v]) => v.p95));
  return (
    <Card
      title="Overhead per stage"
      subtitle={total ? `Whole check: p50 ${total.p50} ms, p95 ${total.p95} ms over ${total.count} requests` : "Since the gateway started"}
    >
      {stages.length === 0 ? (
        <Empty title="No traffic yet" hint="Timings appear after the first request." />
      ) : (
        <ul className="space-y-2.5">
          {stages.map(([k, v]) => (
            <li key={k} className="text-xs">
              <div className="mb-1 flex justify-between gap-2">
                <span className="truncate text-ink2">{k.startsWith("control:") ? `${title(k.slice(8))} check` : k.replace(/_/g, " ")}</span>
                <span className="shrink-0 tabular-nums text-muted">
                  <b className="font-medium text-ink">{v.p50}</b> / {v.p95} ms
                </span>
              </div>
              <div className="relative h-2 overflow-hidden rounded-full bg-ink/[0.06]" title={`p50 ${v.p50} ms, p95 ${v.p95} ms, max ${v.max} ms`}>
                <span className="absolute inset-y-0 left-0 rounded-full bg-accent/30" style={{ width: `${(100 * v.p95) / max}%` }} />
                <span className="absolute inset-y-0 left-0 rounded-full bg-accent" style={{ width: `${(100 * v.p50) / max}%` }} />
              </div>
            </li>
          ))}
          <li className="flex gap-3 pt-1 text-[11px] text-muted">
            <span className="inline-flex items-center gap-1">
              <span className="h-2 w-3 rounded-full bg-accent" /> p50
            </span>
            <span className="inline-flex items-center gap-1">
              <span className="h-2 w-3 rounded-full bg-accent/30" /> p95
            </span>
          </li>
        </ul>
      )}
    </Card>
  );
}
