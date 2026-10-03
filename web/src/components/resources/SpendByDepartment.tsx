import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import type { CatalogItem } from "../../api";
import { ops } from "../../opsApi";
import { countC, usdC } from "../../lib/compact";
import { BarList } from "../charts";
import { ErrorBox, Loading } from "../ui";

/** 30-day spend per department, each with its biggest resources underneath. */
export function SpendByDepartment({ catalog, days = 30 }: { catalog: Record<string, CatalogItem>; days?: number }) {
  const q = useQuery({
    queryKey: ["admin", "usage", "department,resource", days],
    // Two group-bys at once when the server allows it; otherwise departments alone.
    queryFn: () => ops.usage("department,resource", days).catch(() => ops.usage("department", days)),
    refetchInterval: 60_000,
  });
  const rows = useMemo(() => {
    const m = new Map<string, { usd: number; tokens: number; res: Map<string, number> }>();
    for (const r of q.data ?? []) {
      const d = String(r.department ?? "Unassigned");
      const e = m.get(d) ?? { usd: 0, tokens: 0, res: new Map() };
      e.usd += r.usd || 0;
      e.tokens += r.tokens || 0;
      if (r.resource) e.res.set(String(r.resource), (e.res.get(String(r.resource)) ?? 0) + (r.usd || 0));
      m.set(d, e);
    }
    return [...m].map(([k, e]) => ({ key: k, ...e })).sort((a, b) => b.usd - a.usd);
  }, [q.data]);

  if (q.isPending) return <Loading rows={6} />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const total = rows.reduce((s, r) => s + r.usd, 0);
  return (
    <BarList
      rows={rows.map((r) => ({
        key: r.key,
        value: r.usd,
        sub: (
          <>
            {total > 0 && `${Math.round((r.usd / total) * 100)}% · `}
            {r.tokens > 0 && `${countC(r.tokens)} tokens · `}
            {[...r.res]
              .sort((a, b) => b[1] - a[1])
              .slice(0, 3)
              .map(([k, v]) => `${catalog[k]?.title ?? k} ${usdC(v)}`)
              .join(", ")}
          </>
        ),
      }))}
      fmt={usdC}
      empty="No spend by department yet"
    />
  );
}
