// Traps: decoys planted where people look, the people who touched them, and how traps work.
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { admin } from "../../api";
import { orgPath } from "../../orgApi";
import { IncidentStatusPill } from "../../components/pills";
import { TrapCard } from "../../components/security/Traps";
import { Card, Empty, Kpi, PageHeader, Pill, Q, TableWrap } from "../../components/ui";
import { IconAlert, IconLock, IconUsers } from "../../components/icons";
import { ago } from "../../lib/format";
import { ruleLabel } from "../../lib/security";

export function TrapsPage() {
  const q = useQuery({ queryKey: ["admin", "decoys"], queryFn: admin.decoys, refetchInterval: 10_000 });
  const d = q.data;
  const titles = Object.fromEntries((d?.decoys ?? []).map((x) => [x.name, x.title]));
  return (
    <div className="space-y-6">
      <PageHeader
        title="Traps"
        subtitle="Decoys planted where people and agents look. Nothing legitimate ever touches one, so opening it quarantines at once. Asking for one by name is an alert. The person only ever sees “restricted material”."
      />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="Traps planted" value={d ? d.decoys.length : "…"} icon={<IconLock />} />
        <Kpi label="Touches, all time" value={d ? d.touches : "…"} tone={d?.touches ? "bad" : undefined} icon={<IconAlert />} />
        <Kpi label="People caught" value={d ? d.caught : "…"} sub="opened a trap, not just asked" tone={d?.caught ? "bad" : undefined} icon={<IconUsers />} />
        <Kpi label="Need review" value={d ? d.open : "…"} sub="open or acknowledged" tone={d?.open ? "warn" : undefined} />
      </div>

      <Q q={q} rows={4}>
        {(x) =>
          x.decoys.length === 0 ? (
            <Card>
              <Empty title="No traps planted" hint="Add decoys under `decoys:` in policy.yaml." />
            </Card>
          ) : (
            <div className="grid gap-4 md:grid-cols-2">
              {x.decoys.map((t) => (
                <TrapCard key={t.name} d={t} />
              ))}
            </div>
          )
        }
      </Q>

      <Card title="Every touch" subtitle="Newest first. Open one for its evidence: the exact call or prompt." flush>
        <Q q={q} rows={6}>
          {(x) =>
            x.events.length === 0 ? (
              <Empty title="Nobody has touched a trap" hint="When someone does, it shows up here within seconds." />
            ) : (
              <TableWrap>
                <table className="tbl min-w-[760px]">
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Person</th>
                      <th>Trap</th>
                      <th>What happened</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {x.events.map((e) => (
                      <tr key={e.id}>
                        <td className="whitespace-nowrap text-muted">
                          <Link to={`/console/incidents/${encodeURIComponent(e.id)}`} className="hover:text-accent">
                            {ago(e.ts)}
                          </Link>
                        </td>
                        <td>
                          <Link to={orgPath.person(e.principal)} className="font-medium text-ink hover:text-accent">
                            {e.name || e.principal}
                          </Link>
                          <div className="text-xs text-muted">{[e.team, e.department].filter(Boolean).join(" · ")}</div>
                        </td>
                        <td className="text-ink2">{(e.trap && titles[e.trap]) || "—"}</td>
                        <td>
                          <div className="flex items-center gap-2">
                            <Pill tone={e.rule === "decoy_touch" ? "bad" : "warn"} dot>
                              {ruleLabel(e.rule)}
                            </Pill>
                            <span className="truncate text-xs text-muted" title={e.detail}>
                              {e.detail.replace(/ the decoy '[^']*'/, "")}
                            </span>
                          </div>
                        </td>
                        <td>
                          <IncidentStatusPill status={e.status} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )
          }
        </Q>
      </Card>

      <Card title="How traps work">
        <ul className="list-disc space-y-1.5 pl-5 text-sm text-ink2">
          <li>Each trap is listed where people look (document search, the data catalog), so finding one is harmless.</li>
          <li>
            A tool call that names it, or its content showing up in a prompt or tool call, is <b>Trap opened</b>: a
            high-severity incident and quarantine at once.
          </li>
          <li>A prompt asking for it by name is <b>Trap asked for</b>: an alert, since it may just be curiosity.</li>
          <li>The gateway answers an opening call with convincing fake content; nothing real is behind a trap.</li>
          <li>Traps are defined under <code className="font-mono text-[12px]">decoys:</code> in policy.yaml.</li>
        </ul>
      </Card>
    </div>
  );
}
