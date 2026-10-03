import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type { ActivityEvent } from "../api";
import { ago, dateTime, timeOfDay } from "../lib/format";
import { costLine, describe, kindLabel } from "../lib/events";
import { DecisionPill, SeverityPill, SourceBadge, sourceLabel } from "./pills";
import { Button, Empty, ErrorBox, Loading, Select, cx } from "./ui";
import { IconPause, IconPlay } from "./icons";

export interface FeedFilters {
  source?: string;
  severity?: string;
  kind?: string;
  principal?: string;
}

const SOURCES = ["gateway", "mcp", "claude_code", "report", "billing_export", "detections", "cloudevents"];
const SEVERITIES = ["high", "medium", "low", "info"];

const sevBorder = (s: string | null) =>
  s === "high" ? "border-l-bad" : s === "medium" ? "border-l-serious" : s === "low" ? "border-l-warn" : "border-l-transparent";

export function FeedRow({ e, linkPeople, fresh, onClick }: { e: ActivityEvent; linkPeople?: boolean; fresh?: boolean; onClick?: () => void }) {
  const cc = e.source === "claude_code";
  const check = e.kind.startsWith("check.");
  const cost = costLine(e);
  return (
    <li
      onClick={onClick}
      className={cx(
        "grid grid-cols-[52px_minmax(0,1fr)_auto] items-start gap-x-3 border-b border-l-2 border-b-line/60 px-3 py-2 text-xs last:border-b-0 sm:grid-cols-[64px_108px_minmax(0,1fr)_auto]",
        sevBorder(e.severity),
        cc && "bg-cc/[0.045]",
        e.evidence && "bg-bad/[0.07]",
        fresh && "flash-in",
        onClick && "cursor-pointer hover:bg-raised/70",
      )}
    >
      <time className="tnum pt-0.5 text-muted" title={dateTime(e.ts)}>
        {new Date(e.ts * 1000).toDateString() === new Date().toDateString() ? timeOfDay(e.ts).slice(0, 5) : ago(e.ts)}
      </time>
      <div className="hidden pt-px sm:block">
        <SourceBadge source={e.source} />
      </div>
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="sm:hidden">
            <SourceBadge source={e.source} />
          </span>
          <span className={cx("shrink-0 font-medium", cc ? "text-cc" : "text-ink")}>{kindLabel(e.kind)}</span>
          <span className="truncate text-ink2" title={describe(e)}>
            {describe(e)}
          </span>
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px] text-muted">
          {e.principal &&
            (linkPeople ? (
              <Link to={`/console/people/${encodeURIComponent(e.principal)}`} onClick={(ev) => ev.stopPropagation()} className="font-medium text-ink2 hover:text-accent">
                {e.principal}
              </Link>
            ) : (
              <span className="font-medium text-ink2">{e.principal}</span>
            ))}
          {e.workflow && <span>{e.workflow === "(none)" ? "unattributed" : e.workflow}</span>}
          {e.task && <span className="font-mono">{e.task}</span>}
          {e.evidence && <span className="font-semibold uppercase tracking-wide text-bad">evidence</span>}
        </div>
      </div>
      <div className="flex flex-col items-end gap-1">
        {check ? <DecisionPill decision={e.decision} /> : e.severity && e.severity !== "info" ? <SeverityPill severity={e.severity} /> : null}
        {cost && <span className="tnum whitespace-nowrap text-[11px] text-ink2">{cost}</span>}
      </div>
    </li>
  );
}

/**
 * The live activity feed: polls the newest page, highlights arrivals, and pages back with `before`.
 * `load` decides admin vs employee; `serverFilters` says whether filters go to the server or are applied here.
 */
export function ActivityFeed({
  load,
  queryKey,
  filters: showFilters = true,
  serverFilters = true,
  fixed,
  linkPeople,
  limit = 50,
  maxH = "560px",
  interval = 3000,
  emptyHint,
}: {
  load: (p: FeedFilters & { limit: number; before?: number }) => Promise<ActivityEvent[]>;
  queryKey: unknown[];
  filters?: boolean;
  serverFilters?: boolean;
  fixed?: FeedFilters;
  linkPeople?: boolean;
  limit?: number;
  maxH?: string;
  interval?: number;
  emptyHint?: string;
}) {
  const [f, setF] = useState<FeedFilters>({});
  const [live, setLive] = useState(true);
  const [older, setOlder] = useState<ActivityEvent[]>([]);
  const [olderErr, setOlderErr] = useState<unknown>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [exhausted, setExhausted] = useState(false);
  const seen = useRef<Set<string> | null>(null);
  const [fresh, setFresh] = useState<Set<string>>(new Set());

  const eff: FeedFilters = { ...f, ...fixed };
  const params = serverFilters ? eff : {};
  const q = useQuery({
    queryKey: [...queryKey, "feed", params, limit],
    queryFn: () => load({ ...params, limit }),
    refetchInterval: live ? interval : false,
  });

  useEffect(() => {
    setOlder([]);
    setExhausted(false);
    seen.current = null;
  }, [JSON.stringify(eff)]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!q.data) return;
    const ids = q.data.map((e) => e.id);
    if (seen.current) {
      const n = new Set(ids.filter((id) => !seen.current!.has(id)));
      if (n.size) setFresh(n);
      ids.forEach((id) => seen.current!.add(id));
    } else {
      seen.current = new Set(ids);
    }
  }, [q.data]);

  const match = (e: ActivityEvent) =>
    serverFilters ||
    ((!eff.source || e.source === eff.source) &&
      (!eff.severity || e.severity === eff.severity) &&
      (!eff.kind || e.kind === eff.kind) &&
      (!eff.principal || e.principal === eff.principal));

  const head = q.data ?? [];
  const lastHead = head.length ? head[head.length - 1].ts : undefined;
  const rows = [...head, ...older.filter((e) => lastHead === undefined || e.ts < lastHead)].filter(match);

  const loadOlder = async () => {
    const all = [...head, ...older];
    const before = all.length ? all[all.length - 1].ts : undefined;
    setLoadingOlder(true);
    setOlderErr(null);
    try {
      const page = await load({ ...params, limit: 100, before });
      if (page.length < 100) setExhausted(true);
      setOlder((o) => [...o, ...page]);
    } catch (e) {
      setOlderErr(e);
    } finally {
      setLoadingOlder(false);
    }
  };

  return (
    <div>
      {showFilters && (
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
          {!fixed?.source && (
            <Select
              label="Source"
              value={f.source ?? ""}
              onChange={(v) => setF((x) => ({ ...x, source: v || undefined }))}
              options={[{ value: "", label: "All sources" }, ...SOURCES.map((s) => ({ value: s, label: sourceLabel(s) }))]}
            />
          )}
          {!fixed?.severity && <Select
            label="Severity"
            value={f.severity ?? ""}
            onChange={(v) => setF((x) => ({ ...x, severity: v || undefined }))}
            options={[{ value: "", label: "Any severity" }, ...SEVERITIES.map((s) => ({ value: s, label: s }))]}
          />}
          <div className="ml-auto flex items-center gap-2 text-[11px] text-muted">
            {live && (
              <span className="inline-flex items-center gap-1.5">
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-good opacity-60" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-good" />
                </span>
                live
              </span>
            )}
            <Button size="sm" variant="ghost" onClick={() => setLive((l) => !l)} aria-label={live ? "Pause feed" : "Resume feed"}>
              {live ? <IconPause size={14} /> : <IconPlay size={14} />}
              {live ? "Pause" : "Resume"}
            </Button>
          </div>
        </div>
      )}
      {q.isPending ? (
        <div className="p-3">
          <Loading rows={6} />
        </div>
      ) : q.isError ? (
        <div className="p-3">
          <ErrorBox error={q.error} retry={() => q.refetch()} />
        </div>
      ) : rows.length === 0 ? (
        <Empty title="No activity" hint={emptyHint ?? "Events appear here as soon as they happen."} />
      ) : (
        <div className="overflow-y-auto" style={{ maxHeight: maxH }}>
          <ul>
            {rows.map((e) => (
              <FeedRow key={e.id} e={e} linkPeople={linkPeople} fresh={fresh.has(e.id)} />
            ))}
          </ul>
          <div className="flex justify-center p-3">
            {olderErr != null && <ErrorBox error={olderErr} compact />}
            {!exhausted && head.length >= limit && (
              <Button size="sm" variant="ghost" onClick={loadOlder} disabled={loadingOlder}>
                {loadingOlder ? "Loading…" : "Load older"}
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
