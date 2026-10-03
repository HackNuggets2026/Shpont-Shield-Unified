import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { admin } from "../../api";
import { ActivityFeed } from "../../components/ActivityFeed";
import { IncidentTable } from "../../components/IncidentList";
import { LevelPill } from "../../components/pills";
import { Card, Empty, Kpi, PageHeader, Q, Segmented, Select } from "../../components/ui";
import { IconAlert, IconRadar, IconUsers } from "../../components/icons";
import { titleCase } from "../../lib/format";
import { RiskBar } from "./People";

type StatusFilter = "active" | "open" | "acknowledged" | "resolved" | "dismissed" | "all";

export function Security() {
  const q = useQuery({ queryKey: ["admin", "incidents"], queryFn: () => admin.incidents(), refetchInterval: 5_000 });
  const people = useQuery({ queryKey: ["admin", "principals"], queryFn: admin.principals, refetchInterval: 10_000 });
  const [status, setStatus] = useState<StatusFilter>("active");
  const [sev, setSev] = useState("");
  const [rule, setRule] = useState("");
  const [who, setWho] = useState("");

  const all = q.data?.incidents ?? [];
  const rules = useMemo(() => [...new Set(all.map((i) => i.rule))].sort(), [all]);
  const persons = useMemo(() => [...new Set(all.map((i) => i.principal))].sort(), [all]);
  const counts = {
    open: all.filter((i) => i.status === "open").length,
    ack: all.filter((i) => i.status === "acknowledged").length,
    high: all.filter((i) => i.status === "open" && i.severity === "high").length,
  };
  const risky = (people.data ?? []).filter((p) => p.risk > 0).slice(0, 8);

  return (
    <div className="space-y-4">
      <PageHeader title="Security" subtitle="Usage that looks like an attack: probing, exfiltration, stolen keys, tool drift, spend spikes." />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="Open incidents" icon={<IconAlert />} tone={counts.open ? "bad" : undefined} value={counts.open} sub={`${counts.high} high severity`} />
        <Kpi label="Acknowledged" value={counts.ack} sub="being looked at" />
        <Kpi label="Rules triggered" icon={<IconRadar />} value={rules.length} sub={rules.slice(0, 3).map(titleCase).join(", ") || "none"} />
        <Kpi
          label="People with risk"
          icon={<IconUsers />}
          tone={risky.some((p) => p.level !== "none") ? "warn" : undefined}
          value={risky.length}
          sub={`${risky.filter((p) => p.level !== "none").length} past a response level`}
          to="/console/people"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card
          flush
          className="xl:col-span-2"
          title="Incidents"
          actions={
            <>
              <Segmented<StatusFilter>
                value={status}
                onChange={setStatus}
                options={[
                  { value: "active", label: "Active" },
                  { value: "open", label: "Open" },
                  { value: "resolved", label: "Resolved" },
                  { value: "dismissed", label: "Dismissed" },
                  { value: "all", label: "All" },
                ]}
              />
              <Select label="Severity" value={sev} onChange={setSev} options={[{ value: "", label: "Any" }, ...["high", "medium", "low"].map((s) => ({ value: s, label: s }))]} />
              <Select label="Rule" value={rule} onChange={setRule} options={[{ value: "", label: "Any rule" }, ...rules.map((r) => ({ value: r, label: titleCase(r) }))]} />
              <Select label="Person" value={who} onChange={setWho} options={[{ value: "", label: "Anyone" }, ...persons.map((r) => ({ value: r, label: r }))]} />
            </>
          }
        >
          <Q q={q} rows={6}>
            {(d) => {
              const rows = d.incidents.filter(
                (i) =>
                  (status === "all" || (status === "active" ? i.status === "open" || i.status === "acknowledged" : i.status === status)) &&
                  (!sev || i.severity === sev) &&
                  (!rule || i.rule === rule) &&
                  (!who || i.principal === who),
              );
              return <IncidentTable incidents={rows} empty={d.incidents.length ? "No incidents match the filters" : "No incidents. All quiet."} />;
            }}
          </Q>
        </Card>

        <Card title="Risk scores" subtitle="Decaying sum of incident weights">
          <Q q={people} rows={5}>
            {() =>
              risky.length === 0 ? (
                <Empty title="Nobody carries risk right now" />
              ) : (
                <ul className="space-y-2.5">
                  {risky.map((p) => (
                    <li key={p.principal} className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <Link to={`/console/people/${encodeURIComponent(p.principal)}`} className="font-medium text-ink hover:text-accent">
                          {p.principal}
                        </Link>
                        <div className="text-[11px] text-muted">
                          {p.team} · {p.open_incidents} open
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <LevelPill level={p.level} />
                        <RiskBar score={p.risk} />
                      </div>
                    </li>
                  ))}
                </ul>
              )
            }
          </Q>
          {q.data?.levels && <p className="mt-4 text-[11px] text-muted">Response levels: {q.data.levels.join(" → ")}</p>}
        </Card>
      </div>

      <Card title="Detections and high-severity events" flush>
        <ActivityFeed load={admin.activity} queryKey={["admin", "activity", "security"]} fixed={{ severity: "high" }} linkPeople maxH="420px" emptyHint="High-severity events (blocked attacks, exfiltration) show up here." />
      </Card>
    </div>
  );
}
