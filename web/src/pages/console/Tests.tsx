// Tests: the scenario self-test (runs on every policy change) and the latest pytest run, per control.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { selftest, type PytestCase, type PytestReport, type SelfTestRun, type SelfTestScenario, type SelfTestStep } from "../../api";
import { IconCheck, IconDownload, IconPlay } from "../../components/icons";
import { Button, Card, Empty, ErrorBox, Mono, PageHeader, Pill, Q, TableWrap, TONE_COLOR, Wash, cx, type Tone } from "../../components/ui";
import { ago } from "../../lib/format";

const VERDICT_TONE: Record<string, Tone> = { allow: "good", log: "info", warn: "warn", redact: "serious", block: "bad", error: "neutral" };
const DOT_BG: Record<string, string> = { allow: "bg-good", log: "bg-info", warn: "bg-warn", redact: "bg-serious", block: "bg-bad", error: "bg-muted" };
const short = (v: string | null | undefined) => (v ? v.slice(0, 7) : "none");

export function TestsPage() {
  const qc = useQueryClient();
  const latest = useQuery({
    queryKey: ["admin", "selftest", "latest"],
    queryFn: selftest.latest,
    refetchInterval: (q) => (q.state.data?.status.pending || q.state.data?.status.running ? 1_000 : 10_000),
  });
  const history = useQuery({ queryKey: ["admin", "selftest", "history", latest.data?.result?.run_id], queryFn: selftest.history });
  const run = useMutation({
    mutationFn: selftest.run,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "selftest"] }),
  });
  const st = latest.data?.status;
  const res = latest.data?.result ?? null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Tests"
        subtitle="Attack and clean-traffic scenarios run against the live policy, as each person, without spending budget or raising risk. They run again by themselves a moment after every policy change, so a weakened control shows up here as a regression."
        actions={
          <Button variant="primary" onClick={() => run.mutate()} disabled={run.isPending || st?.running}>
            <IconPlay size={14} /> {run.isPending || st?.running ? "Running…" : "Run now"}
          </Button>
        }
      />
      {run.isError && <ErrorBox error={run.error} compact />}

      <Q q={latest} rows={2}>
        {(d) => <StatusStrip status={d.status} res={d.result} />}
      </Q>

      {res && <Regressions res={res} />}

      {res ? (
        <div className="grid items-start gap-4 md:grid-cols-2">
          {res.scenarios.map((sc) => (
            <ScenarioCard key={sc.id} sc={sc} regressed={new Set(res.regressions.filter((r) => r.scenario === sc.id).map((r) => r.step))} />
          ))}
        </div>
      ) : (
        latest.isSuccess && (
          <Card>
            <Empty title="No self-test has run yet" hint="Press Run now, or change the policy: the scenarios run by themselves a moment later." />
          </Card>
        )
      )}

      {history.data && history.data.runs.length > 1 && (
        <Card title="Recent runs" subtitle="The last 20, newest first. Each is keyed by the policy version it ran on." flush>
          <TableWrap>
            <table className="tbl min-w-[640px]">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Trigger</th>
                  <th>Policy</th>
                  <th>Steps passing</th>
                  <th>Regressions</th>
                </tr>
              </thead>
              <tbody>
                {history.data.runs.map((r) => (
                  <tr key={r.run_id}>
                    <td className="whitespace-nowrap text-muted">{ago(r.started_at)}</td>
                    <td>
                      <Pill tone={r.trigger === "auto" ? "accent" : "neutral"}>{r.trigger === "auto" ? "policy change" : r.trigger}</Pill>
                    </td>
                    <td>
                      <Mono>{r.prev_version ? `${short(r.prev_version)} → ${short(r.policy_version)}` : short(r.policy_version)}</Mono>
                    </td>
                    <td className="tabular-nums">
                      {r.summary.passed}/{r.summary.steps}
                      {r.summary.failed > 0 && <span className="ml-2 text-bad">{r.summary.failed} failing</span>}
                    </td>
                    <td>{r.summary.regressions > 0 ? <Pill tone="bad">{r.summary.regressions}</Pill> : <span className="text-muted">0</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Card>
      )}

      <PytestSection />
    </div>
  );
}

function StatusStrip({ status, res }: { status: NonNullable<Awaited<ReturnType<typeof selftest.latest>>>["status"]; res: SelfTestRun | null }) {
  const s = res?.summary;
  const ok = s ? s.failed === 0 : null;
  return (
    <section className="soft-card relative overflow-hidden rounded-2xl px-5 py-4">
      <Wash color={TONE_COLOR[ok === null ? "accent" : ok ? "good" : "bad"]} opacity={0.08} />
      <div className="relative flex flex-wrap items-center gap-x-8 gap-y-3 text-sm">
        <Fact label="Policy version">
          <Mono>{status.policy_version}</Mono>
          {res && res.policy_version !== status.policy_version && <span className="ml-2 text-xs text-warn">last run was on {short(res.policy_version)}</span>}
        </Fact>
        <Fact label="Last run">
          {res
            ? `${ago(res.started_at)} · ${res.trigger === "auto" ? "after a policy change" : res.trigger === "startup" ? "at startup" : "by hand"} · ${Math.round(res.duration_ms)} ms${res.semantic_backend ? ` · ${res.semantic_backend.replace(/Backend$/, "")} semantic tier` : ""}`
            : "never"}
        </Fact>
        <Fact label="Auto-run">
          {status.running ? (
            <Pill tone="accent" dot>
              running
            </Pill>
          ) : status.pending ? (
            <Pill tone="warn" dot>
              queued after change from {short(status.pending.from)}
            </Pill>
          ) : status.auto ? (
            <Pill tone="good" dot>
              on policy change, after {status.debounce_seconds} s
            </Pill>
          ) : (
            <Pill>off (ACL_SELFTEST_AUTO=0)</Pill>
          )}
        </Fact>
        {s && (
          <Fact label="Result">
            <span className={cx("font-medium", ok ? "text-good" : "text-bad")}>
              {s.passed} of {s.steps} steps pass
            </span>
            <span className="text-muted">
              {" "}
              · {s.scenarios_passed}/{s.scenarios} scenarios{s.failed ? ` · ${s.failed} failing` : ""}
              {s.known_gaps ? ` · ${s.known_gaps} known gap${s.known_gaps === 1 ? "" : "s"}` : ""}
            </span>
          </Fact>
        )}
      </div>
      {res?.error && <p className="relative mt-2 text-xs text-bad">{res.error}</p>}
    </section>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-0.5 text-ink">{children}</div>
    </div>
  );
}

function Regressions({ res }: { res: SelfTestRun }) {
  if (!res.prev_version && res.regressions.length === 0) return null;
  const bad = res.regressions.length > 0;
  return (
    <Card tint={TONE_COLOR[bad ? "bad" : "good"]} title={bad ? "Regressions since the last policy change" : "No regressions since the last policy change"} subtitle={res.note}>
      {bad ? (
        <ul className="space-y-1.5 text-sm">
          {res.regressions.map((r) => (
            <li key={`${r.scenario}-${r.step}`} className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-ink">{r.title}</span>
              <span className="text-muted">step {r.step + 1}: {r.why}</span>
              <span className="text-xs text-muted">expected</span>
              {r.expect.map((e) => (
                <Pill key={e} tone={VERDICT_TONE[e] ?? "neutral"}>
                  {e}
                </Pill>
              ))}
              <span className="text-xs text-muted">got</span>
              <Pill tone={VERDICT_TONE[r.actual] ?? "neutral"} dot>
                {r.actual}
              </Pill>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-ink2">Every step that passed on the previous policy still passes.</p>
      )}
    </Card>
  );
}

function ScenarioCard({ sc, regressed }: { sc: SelfTestScenario; regressed: Set<number> }) {
  const [open, setOpen] = useState(false);
  const failing = sc.steps.filter((s) => !s.passed && !s.known_gap).length;
  const gaps = sc.steps.filter((s) => !s.passed && s.known_gap).length;
  return (
    <section className="soft-card relative min-w-0 overflow-hidden rounded-2xl">
      <Wash color={TONE_COLOR[failing ? "bad" : "good"]} opacity={failing ? 0.1 : 0.05} />
      <button type="button" onClick={() => setOpen(!open)} className="relative block w-full px-5 py-4 text-left" aria-expanded={open}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold text-ink">{sc.title}</h2>
            <p className="mt-0.5 text-xs text-muted">{sc.why}</p>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            {gaps > 0 && <Pill tone="warn">{gaps} known gap{gaps === 1 ? "" : "s"}</Pill>}
            <Pill tone={failing ? "bad" : "good"} dot>
              {failing ? `${failing} failing` : "pass"}
            </Pill>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          {sc.steps.map((s, i) => (
            <span
              key={i}
              title={`${i + 1}. ${s.why}: expected ${s.expect.join(" or ")}, got ${s.actual}`}
              className={cx(
                "h-3 w-3 rounded-full",
                DOT_BG[s.actual] ?? "bg-muted",
                !s.passed && !s.known_gap && "ring-2 ring-bad ring-offset-1 ring-offset-panel",
                !s.passed && s.known_gap && "opacity-50 ring-1 ring-warn ring-offset-1 ring-offset-panel",
                regressed.has(i) && "animate-pulse",
              )}
            />
          ))}
          <span className="ml-1 text-xs text-muted">{open ? "hide steps" : `${sc.steps.length} steps`}</span>
        </div>
      </button>
      {open && (
        <ol className="relative divide-y divide-line/60 border-t border-line/60">
          {sc.steps.map((s, i) => (
            <StepRow key={i} i={i} s={s} />
          ))}
        </ol>
      )}
    </section>
  );
}

function StepRow({ i, s }: { i: number; s: SelfTestStep }) {
  const target = s.model ?? s.tool;
  return (
    <li className={cx("px-5 py-3 text-sm", !s.passed && !s.known_gap && "bg-bad/[0.04]")}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs tabular-nums text-muted">{i + 1}.</span>
        <span className="font-medium text-ink">{s.principal}</span>
        <span className="text-xs text-muted">
          {s.direction.replace("_", " ")}
          {target ? ` · ${target}` : ""}
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          {s.passed ? <IconCheck size={14} className="text-good" /> : <Pill tone={s.known_gap ? "warn" : "bad"}>{s.known_gap ? "known gap" : "fail"}</Pill>}
          <span className="text-xs tabular-nums text-muted">{s.latency_ms.toFixed(1)} ms</span>
        </span>
      </div>
      <p className="mt-1 break-words font-mono text-xs text-ink2">{s.text}</p>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
        <span className="text-muted">expected</span>
        {s.expect.map((e) => (
          <Pill key={e} tone={VERDICT_TONE[e] ?? "neutral"}>
            {e}
          </Pill>
        ))}
        {s.control && <span className="text-muted">by {s.control}</span>}
        <span className="ml-2 text-muted">got</span>
        <Pill tone={VERDICT_TONE[s.actual] ?? "neutral"} dot>
          {s.actual}
        </Pill>
        {s.controls.length > 0 && <span className="text-muted">by {s.controls.join(", ")}</span>}
      </div>
      <p className="mt-1 text-xs text-muted">
        {s.why}
        {s.reason && !s.passed ? ` · ${s.reason}` : ""}
      </p>
    </li>
  );
}

// ---- pytest ---------------------------------------------------------------------------------------

interface ControlRow {
  control: string;
  positive: { passed: number; total: number };
  negative: { passed: number; total: number };
  other: { passed: number; total: number };
  failed: number;
}

const passedOutcome = (t: PytestCase) => t.outcome === "passed" || t.outcome === "xfailed" || t.outcome === "xpassed";
const skippedOutcome = (t: PytestCase) => t.outcome === "skipped";
const failedOutcome = (t: PytestCase) => t.outcome === "failed" || t.outcome === "error";

function byControl(r: PytestReport): ControlRow[] {
  const rows = new Map<string, ControlRow>();
  for (const t of r.tests) {
    const key = t.control_id ?? "(unmarked)";
    let row = rows.get(key);
    if (!row) {
      row = { control: key, positive: { passed: 0, total: 0 }, negative: { passed: 0, total: 0 }, other: { passed: 0, total: 0 }, failed: 0 };
      rows.set(key, row);
    }
    if (skippedOutcome(t)) continue;
    const cell = row[t.kind] ?? row.other;
    cell.total += 1;
    if (passedOutcome(t)) cell.passed += 1;
    if (failedOutcome(t)) row.failed += 1;
  }
  return [...rows.values()].sort((a, b) => (a.control === "(unmarked)" ? 1 : b.control === "(unmarked)" ? -1 : a.control.localeCompare(b.control)));
}

function Frac({ c }: { c: { passed: number; total: number } }) {
  if (!c.total) return <span className="text-muted">·</span>;
  return <span className={cx("tabular-nums", c.passed < c.total ? "text-bad" : "text-ink")}>{c.passed}/{c.total}</span>;
}

function PytestSection() {
  const q = useQuery({ queryKey: ["admin", "selftest", "pytest"], queryFn: selftest.pytest, refetchInterval: 30_000 });
  const download = async () => {
    const xml = await selftest.junit();
    const url = URL.createObjectURL(new Blob([xml], { type: "application/xml" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "junit.xml";
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <Q q={q} rows={3}>
      {(d) => {
        const r = d.report;
        if (!r)
          return (
            <Card title="Test suite" subtitle="Unit and integration tests, grouped by the control they exercise.">
              <Empty title="No test report yet" hint={<>Run <Mono>make selftest</Mono> in the repository; the report lands in {d.path}.</>} />
            </Card>
          );
        const rows = byControl(r);
        const failures = r.tests.filter(failedOutcome);
        return (
          <div className="space-y-4">
            <Card
              title="Test suite"
              subtitle={`${r.summary.passed} of ${r.summary.total} passed${r.summary.failed ? `, ${r.summary.failed} failed` : ""}${r.summary.skipped ? `, ${r.summary.skipped} skipped` : ""} · ${r.summary.duration_s} s · Python ${r.python} · ${ago(Date.parse(r.generated_at) / 1000)}. Positive: the control reacts to an attack. Negative: clean input passes.`}
              actions={
                d.junit && (
                  <Button size="sm" onClick={download}>
                    <IconDownload size={14} /> JUnit XML
                  </Button>
                )
              }
              flush
            >
              <TableWrap maxH="28rem">
                <table className="tbl min-w-[560px]">
                  <thead>
                    <tr>
                      <th>Control</th>
                      <th>Positive</th>
                      <th>Negative</th>
                      <th>Other</th>
                      <th>Failed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.control}>
                        <td className={row.control === "(unmarked)" ? "text-muted" : "font-medium text-ink"}>{row.control}</td>
                        <td>
                          <Frac c={row.positive} />
                        </td>
                        <td>
                          <Frac c={row.negative} />
                        </td>
                        <td>
                          <Frac c={row.other} />
                        </td>
                        <td>{row.failed ? <Pill tone="bad">{row.failed}</Pill> : <span className="text-muted">0</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </Card>
            {failures.length > 0 && (
              <Card title="Failures" tint={TONE_COLOR.bad} flush>
                <ul className="divide-y divide-line/60">
                  {failures.map((t) => (
                    <li key={t.nodeid} className="px-5 py-3">
                      <div className="flex flex-wrap items-center gap-2 text-sm">
                        <Pill tone="bad">{t.outcome}</Pill>
                        <span className="font-medium text-ink">{t.title}</span>
                        {t.control_id && <span className="text-xs text-muted">{t.control_id}</span>}
                      </div>
                      <div className="mt-1 break-all font-mono text-[11px] text-muted">{t.nodeid}</div>
                      {t.message && <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-ink/[0.04] p-2 text-[11px] text-ink2">{t.message}</pre>}
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>
        );
      }}
    </Q>
  );
}
