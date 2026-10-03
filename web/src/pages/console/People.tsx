import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { admin } from "../../api";
import { LevelPill, PersonStatusPill, levelTone } from "../../components/pills";
import { Card, Empty, PageHeader, Pill, Q, Select, TableWrap, cx } from "../../components/ui";
import { num, pct, tokens, usd } from "../../lib/format";

export function RiskBar({ score }: { score: number }) {
  const tone = score >= 100 ? "bg-bad" : score >= 60 ? "bg-serious" : score >= 30 ? "bg-warn" : "bg-good/70";
  return (
    <div className="flex items-center gap-2">
      <span className="tnum w-9 text-right text-sm font-semibold text-ink">{Math.round(score)}</span>
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-ink/10">
        <div className={cx("h-full rounded-full", tone)} style={{ width: `${Math.min(score, 120) / 1.2}%` }} />
      </div>
    </div>
  );
}

export function People() {
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["admin", "principals"], queryFn: admin.principals, refetchInterval: 10_000 });
  const adh = useQuery({ queryKey: ["admin", "adherence", "principal", 30], queryFn: () => admin.adherence("principal", 30) });
  const [team, setTeam] = useState("");
  const [search, setSearch] = useState("");
  const teams = useMemo(() => [...new Set((q.data ?? []).map((r) => r.team))].sort(), [q.data]);
  const adhBy = useMemo(() => Object.fromEntries((adh.data?.rows ?? []).map((r) => [r.key, r.adherence])), [adh.data]);

  return (
    <div>
      <PageHeader title="People" subtitle="Everyone and every agent key, ranked by risk. Open a person to restrict or restore access." />
      <Card
        flush
        title="Risk-ranked"
        actions={
          <>
            <input className="input h-7 w-40 py-0 text-xs" placeholder="Search name…" value={search} onChange={(e) => setSearch(e.target.value)} />
            <Select label="Team" value={team} onChange={setTeam} options={[{ value: "", label: "All teams" }, ...teams.map((t) => ({ value: t, label: t }))]} />
          </>
        }
      >
        <Q q={q} rows={8}>
          {(rows) => {
            const shown = rows.filter((r) => (!team || r.team === team) && (!search || r.principal.toLowerCase().includes(search.toLowerCase())));
            if (!shown.length) return <Empty title="Nobody matches" hint={rows.length ? "Clear the filters." : "People appear once they have an API key or usage."} />;
            return (
              <TableWrap>
                <table className="tbl min-w-[900px]">
                  <thead>
                    <tr>
                      <th>Person</th>
                      <th>Risk</th>
                      <th>Level</th>
                      <th>Status</th>
                      <th className="text-right">Budget</th>
                      <th className="text-right">Spend today</th>
                      <th className="text-right">Tokens today</th>
                      <th className="text-right">Adherence 30d</th>
                      <th className="text-right">Incidents</th>
                      <th className="text-right">Running</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((r) => (
                      <tr
                        key={r.principal}
                        className={cx("row-link", levelTone(r.level) === "bad" && "bg-bad/[0.04]")}
                        onClick={() => nav(`/console/people/${encodeURIComponent(r.principal)}`)}
                      >
                        <td>
                          <div className="font-medium text-ink">{r.principal}</div>
                          <div className="text-xs text-muted">
                            {r.team} · {r.role}
                          </div>
                        </td>
                        <td>
                          <RiskBar score={r.risk} />
                        </td>
                        <td>
                          <LevelPill level={r.level} />
                        </td>
                        <td>
                          <PersonStatusPill status={r.status} scale={r.budget_scale} />
                          {r.reason && (
                            <div className="mt-0.5 max-w-[220px] truncate text-[11px] text-muted" title={`${r.reason} — ${r.by}`}>
                              {r.reason}
                            </div>
                          )}
                        </td>
                        <td className="tnum text-right">{pct(r.budget_scale, 0)}</td>
                        <td className="tnum text-right">{usd(r.today.usd)}</td>
                        <td className="tnum text-right">{tokens(r.today.tokens)}</td>
                        <td className="tnum text-right">{adhBy[r.principal] !== undefined ? pct(adhBy[r.principal]) : "—"}</td>
                        <td className="text-right">{r.open_incidents ? <Pill tone="bad">{r.open_incidents} open</Pill> : <span className="text-muted">0</span>}</td>
                        <td className="tnum text-right">{num(r.leases)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            );
          }}
        </Q>
      </Card>
    </div>
  );
}
