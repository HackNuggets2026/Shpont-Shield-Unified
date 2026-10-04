// Reach: what one person or agent can touch, split into the three legs of the lethal trifecta
// (reads private data, takes in outside content, can send data out), and the cheapest single fix.
import { useQuery } from "@tanstack/react-query";
import { admin, type ReachLeg, type ReachSource } from "../api";
import { Card, Pill, TONE_COLOR, cx } from "./ui";

const LEGS: { leg: ReachLeg; label: string; hint: string }[] = [
  { leg: "private", label: "Reads private data", hint: "High-sensitivity resources, personal-data scopes, file reads" },
  { leg: "untrusted", label: "Takes in outside content", hint: "Tools and services outsiders can write into" },
  { leg: "egress", label: "Can send data out", hint: "Web requests, email, scopes that share outside the company" },
];

function via(s: ReachSource): string {
  if (s.via.startsWith("role:")) return `${s.via.slice(5)} role`;
  if (s.via === "entitled") return s.scopes ? `entitled: ${s.scopes.join(", ")}` : "entitled";
  if (s.via.startsWith("grant:")) return `granted by ${s.via.slice(6)}`;
  return s.via;
}

export function ReachCard({ pid }: { pid: string }) {
  const q = useQuery({ queryKey: ["admin", "reach", pid], queryFn: () => admin.reach(pid), retry: false, staleTime: 30_000 });
  const r = q.data;
  if (!r) return null; // not in the directory (usage-only person) or still loading
  const bad = TONE_COLOR.bad;
  return (
    <Card
      title="Reach"
      subtitle="What this identity can touch. All three legs on one identity let a single planted instruction move private data out."
      tint={r.trifecta ? bad : undefined}
      actions={
        r.trifecta ? (
          <Pill tone="bad" dot>
            lethal trifecta
          </Pill>
        ) : (
          <Pill tone="good">no trifecta</Pill>
        )
      }
    >
      <div className="grid gap-3 md:grid-cols-3">
        {LEGS.map(({ leg, label, hint }) => {
          const srcs = r.legs[leg];
          const weakest = r.weakest === leg;
          return (
            <div
              key={leg}
              className={cx(
                "min-w-0 rounded-xl p-3 ring-1 ring-inset",
                srcs.length ? "bg-ink/[0.03] ring-line/70" : "ring-line/40",
                weakest && "ring-bad/40",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className={cx("text-xs font-semibold", srcs.length ? "text-ink" : "text-muted")}>{label}</span>
                <span className="shrink-0 text-[11px] tabular-nums text-muted">{srcs.length || "none"}</span>
              </div>
              <div className="mt-0.5 text-[11px] text-muted">{hint}</div>
              {srcs.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {srcs.map((s) => (
                    <li key={`${s.kind}:${s.id}:${s.via}`} className="flex min-w-0 items-baseline justify-between gap-2 text-xs">
                      <span className={cx("min-w-0 truncate text-ink2", s.kind === "tool" && "font-mono text-[11px]")}>{s.label}</span>
                      <span className="shrink-0 truncate text-[11px] text-muted">{via(s)}</span>
                    </li>
                  ))}
                </ul>
              )}
              {weakest && <div className="mt-2 text-[11px] font-medium text-bad">Weakest leg</div>}
            </div>
          );
        })}
      </div>
      {r.fix && (
        <p className="mt-3 text-sm text-ink2">
          <span className="font-medium text-ink">Cheapest fix:</span> {r.fix}
        </p>
      )}
    </Card>
  );
}
