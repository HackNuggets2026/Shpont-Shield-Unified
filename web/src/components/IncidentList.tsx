import { useNavigate } from "react-router-dom";
import type { Incident } from "../api";
import type { OrgFields } from "../opsApi";
import { ago, dateTime } from "../lib/format";
import { ruleLabel, severityFirst } from "../lib/security";
import { IncidentStatusPill, SeverityPill } from "./pills";
import { Empty, TableWrap, cx } from "./ui";

export function IncidentTable({ incidents, showPerson = true, empty = "No incidents" }: { incidents: (Incident & OrgFields)[]; showPerson?: boolean; empty?: string }) {
  const nav = useNavigate();
  incidents = [...incidents].sort(severityFirst);
  if (!incidents.length) return <Empty title={empty} hint="Detections raise incidents when usage looks like an attack." />;
  return (
    <TableWrap>
      <table className="tbl min-w-[760px]">
        <thead>
          <tr>
            <th>Severity</th>
            <th>Rule</th>
            {showPerson && <th>Person</th>}
            <th>What happened</th>
            <th className="text-right">Weight</th>
            <th>Status</th>
            <th>When</th>
          </tr>
        </thead>
        <tbody>
          {incidents.map((i) => (
            <tr
              key={i.id}
              className={cx("row-link", i.status === "open" && i.severity === "high" && "bg-bad/[0.04]")}
              onClick={() => nav(`/console/incidents/${encodeURIComponent(i.id)}`)}
            >
              <td>
                <SeverityPill severity={i.severity} />
              </td>
              <td className="whitespace-nowrap font-medium text-ink">{ruleLabel(i.rule)}</td>
              {showPerson && (
                <td>
                  <div className="font-medium">{i.name || i.principal}</div>
                  {(i.team || i.department) && <div className="text-[11px] text-muted">{[i.team, i.department].filter(Boolean).join(" · ")}</div>}
                </td>
              )}
              <td className="max-w-[360px]">
                <span className="line-clamp-2 text-xs text-ink2" title={i.detail}>
                  {i.detail}
                </span>
              </td>
              <td className="tnum text-right">{Math.round(i.weight)}</td>
              <td>
                <IncidentStatusPill status={i.status} />
              </td>
              <td className="whitespace-nowrap text-xs text-muted" title={dateTime(i.ts)}>
                {ago(i.ts)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}
