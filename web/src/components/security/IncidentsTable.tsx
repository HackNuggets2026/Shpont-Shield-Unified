import { Link, useNavigate } from "react-router-dom";
import { ago, dateTime } from "../../lib/format";
import { ruleLabel, ruleWhat, RULES, type SecIncident } from "../../lib/security";
import { IncidentStatusPill, SeverityPill } from "../pills";
import { Empty, cx } from "../ui";
import { IconTerminal } from "../icons";

const shortStamp = (ts: number) =>
  new Date(ts * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

const href = (i: SecIncident) => `/console/incidents/${encodeURIComponent(i.id)}`;

function RuleCell({ rule }: { rule: string }) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1.5 font-medium text-ink">
        {RULES[rule]?.cc && <IconTerminal size={12} className="shrink-0 text-cc" aria-label="Claude Code" />}
        <span className="truncate">{ruleLabel(rule)}</span>
      </div>
      <div className="font-mono text-[10.5px] text-muted" title={ruleWhat(rule)}>
        {rule}
      </div>
    </div>
  );
}

/** Incidents for triage: a table from tablet width up, stacked cards on a phone. */
export function IncidentsTable({ incidents, empty, hint }: { incidents: SecIncident[]; empty: string; hint?: string }) {
  const nav = useNavigate();
  if (!incidents.length) return <Empty title={empty} hint={hint} />;
  const hot = (i: SecIncident) => i.status === "open" && i.severity === "high";
  return (
    <>
      <ul className="divide-y divide-line/70 md:hidden">
        {incidents.map((i) => (
          <li key={i.id} className={cx(hot(i) && "bg-bad/[0.05]")}>
            <Link to={href(i)} className="block px-4 py-3 active:bg-raised">
              <div className="flex items-start justify-between gap-2">
                <RuleCell rule={i.rule} />
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <SeverityPill severity={i.severity} />
                  <IncidentStatusPill status={i.status} />
                </div>
              </div>
              <p className="mt-1 line-clamp-2 text-xs text-ink2">{i.detail}</p>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-2 text-[11px] text-muted">
                <span className="font-medium text-ink2">{i.principal}</span>
                <span>weight {Math.round(i.weight)}</span>
                <span title={dateTime(i.ts)}>{ago(i.ts)}</span>
                {i.archived && <span>older than 7 days</span>}
              </div>
            </Link>
          </li>
        ))}
      </ul>
      <div className="hidden overflow-x-auto md:block">
        <table className="tbl">
          <thead>
            <tr>
              <th className="w-[84px]">Severity</th>
              <th>Incident</th>
              <th>What happened</th>
              <th className="text-right">Weight</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {incidents.map((i) => (
              <tr key={i.id} className={cx("row-link", hot(i) && "bg-bad/[0.05]")} onClick={() => nav(href(i))}>
                <td>
                  <SeverityPill severity={i.severity} />
                </td>
                <td className="max-w-[210px]">
                  <Link to={href(i)} onClick={(e) => e.stopPropagation()} className="flex items-center gap-1.5 font-medium text-ink hover:text-accent" title={ruleWhat(i.rule)}>
                    {RULES[i.rule]?.cc && <IconTerminal size={12} className="shrink-0 text-cc" />}
                    <span className="truncate">{ruleLabel(i.rule)}</span>
                  </Link>
                  <Link
                    to={`/console/people/${encodeURIComponent(i.principal)}`}
                    onClick={(e) => e.stopPropagation()}
                    className="text-xs font-medium text-ink2 hover:text-accent"
                  >
                    {i.principal}
                  </Link>
                </td>
                <td className="min-w-[180px] max-w-[320px]">
                  <span className="line-clamp-2 text-xs text-ink2" title={i.detail}>
                    {i.detail}
                  </span>
                  {i.note && <span className="mt-0.5 block truncate text-[11px] italic text-muted">“{i.note}”</span>}
                </td>
                <td className="tnum text-right">{Math.round(i.weight)}</td>
                <td className="whitespace-nowrap">
                  <IncidentStatusPill status={i.status} />
                  <div className="mt-1 text-[11px] text-muted" title={dateTime(i.ts)}>
                    <span className="text-ink2">{ago(i.ts)}</span> · {shortStamp(i.ts)}
                  </div>
                  {i.archived && (
                    <div className="text-[10.5px] text-muted" title="Older than the 7-day risk window: listed from the activity log and not counted in risk scores">
                      archived · not scored
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
