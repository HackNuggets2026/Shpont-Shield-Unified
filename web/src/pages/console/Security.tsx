import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { admin, type PrincipalRow } from "../../api";
import { ActivityFeed } from "../../components/ActivityFeed";
import { AdminLog } from "../../components/AdminLog";
import { LevelPill, PersonStatusPill } from "../../components/pills";
import { Card, Empty, ErrorBox, Kpi, Loading, PageHeader, Pill, Q, Segmented, Select, cx } from "../../components/ui";
import { IconAlert, IconLock, IconRadar, IconUsers } from "../../components/icons";
import { IncidentsTable } from "../../components/security/IncidentsTable";
import { RiskMeter } from "../../components/security/RiskMeter";
import { ago } from "../../lib/format";
import { detectionPolicy, incidentsWithHistory, isAuto, ruleLabel, triageOrder, type SecIncident } from "../../lib/security";

type StatusFilter = "active" | "open" | "acknowledged" | "resolved" | "dismissed" | "all";
const STATUS_FILTERS: StatusFilter[] = ["active", "open", "acknowledged", "resolved", "dismissed", "all"];
const STATUS_LABEL: Record<StatusFilter, string> = {
  active: "Needs attention",
  open: "Open",
  acknowledged: "Acknowledged",
  resolved: "Resolved",
  dismissed: "Dismissed",
  all: "All",
};
const SEVERITIES = ["high", "medium", "low"] as const;

const matchStatus = (f: StatusFilter, s: string) =>
  f === "all" || (f === "active" ? s === "open" || s === "acknowledged" : s === f);

const isRestricted = (p: PrincipalRow) => p.status !== "active" || p.budget_scale < 1;
const atRisk = (p: PrincipalRow) => p.level !== "none" || isRestricted(p) || p.risk > 0;

export function Security() {
  const q = useQuery({ queryKey: ["admin", "incidents", "with-history"], queryFn: incidentsWithHistory, refetchInterval: 5_000 });
  const people = useQuery({ queryKey: ["admin", "principals"], queryFn: admin.principals, refetchInterval: 5_000 });
  const acts = useQuery({ queryKey: ["admin", "actions", "all"], queryFn: () => admin.actions(), refetchInterval: 10_000 });
  const pol = useQuery({ queryKey: ["admin", "policy", "detections"], queryFn: detectionPolicy, staleTime: 60_000 });

  // Filters live in the URL so a filtered list can be linked to (e.g. ?person=frank from a profile).
  const [sp, setSp] = useSearchParams();
  const status = (STATUS_FILTERS.includes(sp.get("status") as StatusFilter) ? sp.get("status") : "active") as StatusFilter;
  const sev = sp.get("severity") ?? "";
  const rule = sp.get("rule") ?? "";
  const who = sp.get("person") ?? "";
  const set = (k: string, v: string, dflt = "") =>
    setSp(
      (prev) => {
        const n = new URLSearchParams(prev);
        if (v === dflt) n.delete(k);
        else n.set(k, v);
        return n;
      },
      { replace: true },
    );
  const filtered = status !== "active" || !!sev || !!rule || !!who;

  const all: SecIncident[] = useMemo(() => [...(q.data?.incidents ?? [])].sort(triageOrder), [q.data]);
  const rules = useMemo(() => [...new Set(all.map((i) => i.rule))].sort((a, b) => ruleLabel(a).localeCompare(ruleLabel(b))), [all]);
  const persons = useMemo(() => [...new Set(all.map((i) => i.principal))].sort(), [all]);
  const rows = all.filter((i) => matchStatus(status, i.status) && (!sev || i.severity === sev) && (!rule || i.rule === rule) && (!who || i.principal === who));

  const open = all.filter((i) => i.status === "open");
  const acked = all.filter((i) => i.status === "acknowledged").length;
  const bySev = Object.fromEntries(SEVERITIES.map((s) => [s, open.filter((i) => i.severity === s).length]));
  const risky = (people.data ?? []).filter(atRisk);
  const leveled = risky.filter((p) => p.level !== "none");
  const restricted = (people.data ?? []).filter(isRestricted);
  const now = Date.now() / 1000;
  const autos24 = (acts.data ?? []).filter((a) => isAuto(a) && now - a.ts < 86400);
  const autoKinds = Object.entries(autos24.reduce<Record<string, number>>((m, a) => ({ ...m, [a.action]: (m[a.action] ?? 0) + 1 }), {}));
  const th = pol.data?.response ?? null;
  const countFor = (f: StatusFilter) => all.filter((i) => matchStatus(f, i.status)).length;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Security"
        subtitle="AI usage that looks like an attack: exfiltration, probing, stolen keys, tool drift, spend spikes. The system responds on its own; you confirm."
      />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi
          label="Open incidents"
          icon={<IconAlert />}
          tone={bySev.high ? "bad" : open.length ? "warn" : undefined}
          value={q.isPending ? "…" : open.length}
          sub={
            <span className="flex flex-wrap items-center gap-1">
              {SEVERITIES.map((s) => (
                <button key={s} type="button" onClick={() => setSp({ status: "open", severity: s }, { replace: true })} className="hover:opacity-80">
                  <Pill tone={bySev[s] ? (s === "high" ? "bad" : s === "medium" ? "serious" : "warn") : "neutral"}>
                    {bySev[s] ?? 0} {s}
                  </Pill>
                </button>
              ))}
              {acked > 0 && <span className="text-[11px]">+{acked} acknowledged</span>}
            </span>
          }
        />
        <Kpi
          label="People at risk"
          icon={<IconUsers />}
          tone={leveled.some((p) => p.level === "quarantine") ? "bad" : leveled.length ? "warn" : undefined}
          value={people.isPending ? "…" : leveled.length}
          sub={
            leveled.length ? (
              <span className="flex flex-wrap items-center gap-1">
                {leveled.slice(0, 3).map((p) => (
                  <Link key={p.principal} to={`/console/people/${encodeURIComponent(p.principal)}`} className="inline-flex items-center gap-1 hover:opacity-80">
                    <span className="font-medium text-ink2">{p.principal}</span>
                    <LevelPill level={p.level} />
                  </Link>
                ))}
                {leveled.length > 3 && <span>+{leveled.length - 3}</span>}
              </span>
            ) : (
              "Nobody past the alert level"
            )
          }
        />
        <Kpi
          label="Auto actions, 24h"
          icon={<IconRadar />}
          tone={autos24.length ? "serious" : undefined}
          value={acts.isPending ? "…" : autos24.length}
          sub={autoKinds.length ? autoKinds.map(([k, n]) => `${n} ${k.replace(/_/g, " ")}`).join(" · ") : "Nothing needed doing"}
        />
        <Kpi
          label="Restricted now"
          icon={<IconLock />}
          tone={restricted.some((p) => p.status !== "active") ? "bad" : restricted.length ? "warn" : undefined}
          value={people.isPending ? "…" : restricted.length}
          sub={restricted.length ? restricted.map((p) => `${p.principal} (${p.status === "active" ? `${Math.round(p.budget_scale * 100)}% budget` : p.status})`).join(", ") : "Everyone has normal access"}
          to="/console/people"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card
          flush
          className="xl:col-span-2"
          title={
            <span>
              Incidents <span className="font-normal text-muted">· {rows.length}</span>
            </span>
          }
          subtitle="Open first, newest first. Click one for its evidence and actions."
          actions={
            <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
              <Select label="Severity" value={sev} onChange={(v) => set("severity", v)} options={[{ value: "", label: "Any" }, ...SEVERITIES.map((s) => ({ value: s, label: s }))]} />
              <Select label="Rule" value={rule} onChange={(v) => set("rule", v)} options={[{ value: "", label: "Any rule" }, ...rules.map((r) => ({ value: r, label: ruleLabel(r) }))]} />
              <Select label="Person" value={who} onChange={(v) => set("person", v)} options={[{ value: "", label: "Anyone" }, ...persons.map((r) => ({ value: r, label: r }))]} />
              {filtered && (
                <button type="button" onClick={() => setSp({}, { replace: true })} className="text-xs font-medium text-accent hover:underline">
                  Reset
                </button>
              )}
            </div>
          }
        >
          <div className="overflow-x-auto border-b border-line px-4 py-2">
            <Segmented<StatusFilter>
              value={status}
              onChange={(v) => set("status", v, "active")}
              options={STATUS_FILTERS.map((f) => ({
                value: f,
                label: (
                  <span className="whitespace-nowrap">
                    {STATUS_LABEL[f]} <span className="tnum text-muted">{q.data ? countFor(f) : ""}</span>
                  </span>
                ),
              }))}
            />
          </div>
          <Q q={q} rows={6}>
            {(d) => (
              <IncidentsTable
                incidents={rows}
                empty={d.incidents.length === 0 ? "No incidents. All quiet." : status === "active" && !sev && !rule && !who ? "Nothing needs attention" : "No incidents match the filters"}
                hint={d.incidents.length === 0 ? "Detections raise incidents when usage looks like an attack." : status === "active" ? "Every incident is resolved or dismissed." : undefined}
              />
            )}
          </Q>
        </Card>

        <div className="min-w-0 space-y-4">
          <Card
            title="People at risk"
            subtitle={pol.data ? `Decaying sum of open incident weights; halves every ${Math.round(pol.data.half_life_minutes)} min` : "Decaying sum of open incident weights"}
            actions={
              <Link to="/console/people" className="text-xs font-medium text-accent hover:underline">
                All people →
              </Link>
            }
            flush
          >
            {people.isPending ? (
              <div className="p-4">
                <Loading rows={4} />
              </div>
            ) : people.isError ? (
              <div className="p-4">
                <ErrorBox error={people.error} retry={() => people.refetch()} />
              </div>
            ) : risky.length === 0 ? (
              <Empty title="Nobody carries risk right now" hint="Scores rise with incidents and decay on their own." />
            ) : (
              <ul className="divide-y divide-line/60">
                {risky.map((p) => (
                  <li key={p.principal}>
                    <Link
                      to={`/console/people/${encodeURIComponent(p.principal)}`}
                      className={cx("flex items-center justify-between gap-3 px-4 py-2.5 hover:bg-raised/60", p.level === "quarantine" && "bg-bad/[0.04]")}
                    >
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="font-medium text-ink">{p.principal}</span>
                          <LevelPill level={p.level} />
                          {isRestricted(p) && <PersonStatusPill status={p.status} scale={p.budget_scale} />}
                        </div>
                        <div className="mt-0.5 truncate text-[11px] text-muted" title={p.reason || undefined}>
                          {p.team} · {p.open_incidents} open
                          {isRestricted(p) && p.by ? ` · by ${p.by}${p.since ? ` ${ago(p.since)}` : ""}` : ""}
                        </div>
                      </div>
                      <RiskMeter score={p.risk} thresholds={th} compact />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            {th && (
              <div className="flex flex-wrap gap-x-3 gap-y-1 border-t border-line px-4 py-2 text-[11px] text-muted">
                <span>
                  alert ≥ <b className="text-ink2">{th.alert}</b>
                </span>
                <span>
                  tighten ≥ <b className="text-ink2">{th.tighten}</b> (budget to {Math.round(th.tighten_budget_scale * 100)}%)
                </span>
                <span>
                  quarantine ≥ <b className="text-ink2">{th.quarantine}</b>
                </span>
                {!th.auto && <span className="text-warn">automatic responses off</span>}
              </div>
            )}
          </Card>

          <Card title="What the system did on its own" subtitle="Automatic responses, newest first" flush>
            <Q q={acts} rows={3}>
              {(d) => (
                <div className="max-h-[300px] overflow-y-auto">
                  <AdminLog actions={d.filter(isAuto).slice(0, 20)} empty="No automatic responses yet" />
                </div>
              )}
            </Q>
          </Card>
        </div>
      </div>

      <Card title="Detections and high-severity events" subtitle="Live: blocked attacks, exfiltration attempts and the incidents they raise" flush>
        <ActivityFeed
          load={admin.activity}
          queryKey={["admin", "activity", "security"]}
          fixed={{ severity: "high" }}
          linkPeople
          maxH="420px"
          emptyHint="High-severity events (blocked attacks, exfiltration) show up here."
        />
      </Card>
    </div>
  );
}
