// Building blocks for server-side paginated, filtered lists at enterprise scale: a pager, org-unit selects,
// a debounced search box, filters kept in the URL, and a person picker that searches the directory.
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ops, type PersonHit } from "../opsApi";
import { countC } from "../lib/compact";
import { Button, Select, cx } from "./ui";
import { IconX } from "./icons";

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/**
 * Filters kept in the URL (so a filtered list can be linked to). Changing any filter resets the page
 * (`<prefix>page`). Defaults are left out of the URL.
 */
export function useUrlFilters<K extends string>(keys: readonly K[], defaults: Partial<Record<K, string>> = {}, prefix = "") {
  const [sp, setSp] = useSearchParams();
  const values = useMemo(() => {
    const o = {} as Record<K, string>;
    for (const k of keys) o[k] = sp.get(prefix + k) ?? defaults[k] ?? "";
    return o;
  }, [sp]); // eslint-disable-line react-hooks/exhaustive-deps
  const page = Math.max(0, Number(sp.get(prefix + "page") ?? 0) || 0);
  const write = (patch: Partial<Record<K | "page", string | number>>) =>
    setSp(
      (prev) => {
        const n = new URLSearchParams(prev);
        if (!("page" in patch)) n.delete(prefix + "page");
        for (const [k, v] of Object.entries(patch) as [string, string | number][]) {
          const dflt = (defaults as Record<string, string>)[k] ?? (k === "page" ? 0 : "");
          if (v === "" || v === dflt || v === undefined) n.delete(prefix + k);
          else n.set(prefix + k, String(v));
        }
        return n;
      },
      { replace: true },
    );
  const reset = () =>
    setSp(
      (prev) => {
        const n = new URLSearchParams(prev);
        for (const k of [...keys, "page"]) n.delete(prefix + k);
        return n;
      },
      { replace: true },
    );
  const dirty = keys.some((k) => values[k] !== (defaults[k] ?? ""));
  return { values, page, set: write, setPage: (p: number) => write({ page: p } as Partial<Record<K | "page", number>>), reset, dirty };
}

/** "1–25 of 1,234" with previous / next. Works without a total (then it only knows whether there is more). */
export function Pager({
  page,
  size,
  shown,
  total,
  hasMore,
  onPage,
  fetching,
  className,
}: {
  page: number;
  size: number;
  shown: number;
  total: number | null;
  hasMore: boolean;
  onPage: (p: number) => void;
  fetching?: boolean;
  className?: string;
}) {
  const from = shown ? page * size + 1 : 0;
  const to = page * size + shown;
  const pages = total !== null ? Math.max(1, Math.ceil(total / size)) : null;
  if (page === 0 && !hasMore && shown < size && (total === null || total <= size)) {
    return total !== null && total > 0 ? (
      <div className={cx("px-4 py-2 text-[11px] text-muted", className)}>
        {countC(total)} {total === 1 ? "row" : "rows"}
      </div>
    ) : null;
  }
  return (
    <div className={cx("flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-xs text-muted", className)}>
      <span className="tnum">
        {from.toLocaleString("en-US")}–{to.toLocaleString("en-US")}
        {total !== null ? <> of {total.toLocaleString("en-US")}</> : hasMore ? " of more" : ""}
        {fetching && <span className="ml-2 animate-pulse">updating…</span>}
      </span>
      <span className="flex items-center gap-1.5">
        {pages !== null && pages > 2 && (
          <Button size="sm" variant="ghost" onClick={() => onPage(0)} disabled={page === 0}>
            First
          </Button>
        )}
        <Button size="sm" onClick={() => onPage(page - 1)} disabled={page === 0}>
          ← Prev
        </Button>
        {pages !== null && (
          <span className="tnum px-1">
            {page + 1} / {pages}
          </span>
        )}
        <Button size="sm" onClick={() => onPage(page + 1)} disabled={!hasMore}>
          Next →
        </Button>
      </span>
    </div>
  );
}

/** Departments of the org, for filter selects (names only; cached for a minute). */
export function useDepartments() {
  const q = useQuery({ queryKey: ["admin", "org", 30], queryFn: () => ops.org(30), staleTime: 60_000 });
  return { names: (q.data?.departments ?? []).map((d) => d.name).sort(), org: q.data, q };
}

export function DepartmentSelect({ value, onChange, label = "Department" }: { value: string; onChange: (v: string) => void; label?: string }) {
  const { names } = useDepartments();
  const opts = value && !names.includes(value) ? [...names, value] : names;
  return <Select label={label} value={value} onChange={onChange} options={[{ value: "", label: "All departments" }, ...opts.map((n) => ({ value: n, label: n }))]} />;
}

export function TeamSelect({ department, value, onChange }: { department: string; value: string; onChange: (v: string) => void }) {
  const q = useQuery({ queryKey: ["admin", "org", "teams", department], queryFn: () => ops.teams(department || undefined, 200), staleTime: 60_000 });
  const names = (q.data?.rows ?? []).map((t) => t.name).sort();
  const opts = value && !names.includes(value) ? [...names, value] : names;
  return <Select label="Team" value={value} onChange={onChange} options={[{ value: "", label: department ? `All teams in ${department}` : "All teams" }, ...opts.map((n) => ({ value: n, label: n }))]} />;
}

/** A text filter that reports its value after the user pauses typing. */
export function SearchBox({ value, onChange, placeholder = "Search people", label = "Person" }: { value: string; onChange: (v: string) => void; placeholder?: string; label?: string }) {
  const [text, setText] = useState(value);
  const debounced = useDebounced(text, 300);
  useEffect(() => setText(value), [value]);
  useEffect(() => {
    if (debounced !== value) onChange(debounced.trim());
  }, [debounced]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <label className="relative inline-flex items-center">
      <span className="sr-only">{label}</span>
      <input
        type="search"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={placeholder}
        className="h-7 w-40 rounded-md border border-line bg-panel px-2 pr-6 text-xs text-ink placeholder:text-muted focus:border-accent focus:outline-none sm:w-44"
      />
      {text && (
        <button type="button" onClick={() => setText("")} className="absolute right-1.5 text-muted hover:text-ink" aria-label="Clear search">
          <IconX size={12} />
        </button>
      )}
    </label>
  );
}

/** "Payments · Engineering" under a person; nothing when the directory does not know them. */
export function OrgLine({ team, department, className }: { team?: string | null; department?: string | null; className?: string }) {
  if (!team && !department) return null;
  return (
    <div className={cx("truncate text-[11px] text-muted", className)} title={[team, department].filter(Boolean).join(" · ")}>
      {[team, department && department !== team ? department : null].filter(Boolean).join(" · ")}
    </div>
  );
}

/** Pick one person out of tens of thousands: type to search the directory. */
export function PersonPicker({ value, onChange }: { value: string; onChange: (pid: string) => void }) {
  const [text, setText] = useState("");
  const q = useDebounced(text.trim(), 250);
  const hits = useQuery({
    queryKey: ["admin", "people", "search", q],
    queryFn: () => ops.people({ q, limit: 8, sort: "name", order: "asc" }),
    enabled: q.length >= 2 && !value,
    staleTime: 30_000,
  });
  if (value)
    return (
      <div className="flex items-center justify-between gap-2 rounded-md border border-line bg-raised px-3 py-2 text-sm">
        <span className="font-medium text-ink">{value}</span>
        <button type="button" className="text-xs text-accent hover:underline" onClick={() => onChange("")}>
          Change
        </button>
      </div>
    );
  const rows: PersonHit[] = hits.data?.rows ?? [];
  return (
    <div>
      <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="Name, email or id (2+ letters)" autoComplete="off" />
      {q.length >= 2 && (
        <ul className="mt-1 max-h-56 overflow-y-auto rounded-md border border-line bg-panel text-sm shadow-sm">
          {hits.isPending ? (
            <li className="px-3 py-2 text-xs text-muted">Searching…</li>
          ) : hits.isError ? (
            <li className="px-3 py-2 text-xs text-bad">{hits.error instanceof Error ? hits.error.message : "Search failed"}</li>
          ) : rows.length === 0 ? (
            <li className="px-3 py-2 text-xs text-muted">Nobody matches “{q}”</li>
          ) : (
            rows.map((p) => (
              <li key={p.principal}>
                <button type="button" className="flex w-full items-baseline justify-between gap-2 px-3 py-1.5 text-left hover:bg-raised" onClick={() => onChange(p.principal)}>
                  <span className="min-w-0 truncate">
                    <span className="font-medium text-ink">{p.name || p.principal}</span>
                    {p.name && <span className="ml-1.5 font-mono text-[11px] text-muted">{p.principal}</span>}
                  </span>
                  <span className="shrink-0 text-[11px] text-muted">{[p.team, p.department].filter(Boolean).join(" · ")}</span>
                </button>
              </li>
            ))
          )}
          {hits.data && hits.data.total > rows.length && <li className="border-t border-line px-3 py-1.5 text-[11px] text-muted">{countC(hits.data.total)} match; keep typing to narrow</li>}
        </ul>
      )}
    </div>
  );
}
