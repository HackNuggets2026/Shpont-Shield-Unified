// Traps: decoys planted where people look, that nothing legitimate ever touches. Each card says where it is
// planted and who touched it; the person who touched it sees only "restricted material".
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { admin, type DecoyRow } from "../../api";
import { orgPath } from "../../orgApi";
import { ago } from "../../lib/format";
import { ruleLabel } from "../../lib/security";
import { Card, Empty, Pill, Q, TONE_COLOR, Wash, cx } from "../ui";
import { IconLock } from "../icons";

const KIND: Record<DecoyRow["kind"], string> = { document: "document", dataset: "dataset", credential: "credential", system: "system" };

export function TrapCard({ d }: { d: DecoyRow }) {
  const hit = d.touches > 0;
  return (
    <div className="soft-card relative overflow-hidden rounded-2xl p-4">
      {hit && <Wash color={TONE_COLOR.bad} opacity={0.08} />}
      <div className="relative flex items-start gap-3">
        <span className={cx("mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl", hit ? "bg-bad/10 text-bad" : "bg-ink/[0.06] text-muted")}>
          <IconLock size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-semibold text-ink">{d.title}</span>
            <Pill tone="neutral">{KIND[d.kind]}</Pill>
          </div>
          <div className="mt-0.5 text-xs text-muted">Planted in {d.planted_in || "an undisclosed place"}</div>
          <div className="mt-0.5 truncate font-mono text-[11px] text-muted" title={d.identifiers.join("\n")}>
            {d.identifiers[0]}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className={cx("tnum text-2xl font-semibold leading-none", hit ? "text-bad" : "text-ink")}>{d.touches}</div>
          <div className="mt-1 text-[11px] text-muted">{d.touches === 1 ? "touch" : "touches"}</div>
        </div>
      </div>
      <div className="relative mt-3 border-t border-line/60 pt-2.5">
        {d.recent.length === 0 ? (
          <div className="text-xs text-muted">Untouched. Nothing legitimate ever opens it.</div>
        ) : (
          <ul className="space-y-1.5">
            {d.recent.slice(0, 4).map((r) => (
              <li key={r.id} className="flex items-center gap-2 text-xs">
                <Pill tone={r.rule === "decoy_touch" ? "bad" : "warn"} dot>
                  {ruleLabel(r.rule)}
                </Pill>
                <Link to={orgPath.person(r.principal)} className="min-w-0 truncate font-medium text-ink hover:text-accent">
                  {r.name || r.principal}
                </Link>
                <span className="truncate text-muted">{r.team ? `· ${r.team}` : ""}</span>
                <Link to={`/console/incidents/${encodeURIComponent(r.id)}`} className="ml-auto shrink-0 text-muted hover:text-accent">
                  {ago(r.ts)}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/** All traps as cards; the Traps page shows them above the list of every touch. */
export function Traps() {
  const q = useQuery({ queryKey: ["admin", "decoys"], queryFn: admin.decoys, refetchInterval: 10_000 });
  return (
    <Card
      title="Traps"
      subtitle="Decoys planted where people look. Nothing legitimate touches one, so opening it restricts the person at once; the person only ever sees “restricted material”."
    >
      <Q q={q} rows={3}>
        {(d) =>
          d.decoys.length === 0 ? (
            <Empty title="No traps planted" hint="Add decoys under `decoys:` in policy.yaml." />
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {d.decoys.map((x) => (
                <TrapCard key={x.name} d={x} />
              ))}
            </div>
          )
        }
      </Q>
    </Card>
  );
}
