import type { CatalogItem } from "../../api";
import { exact, usdC } from "../../lib/compact";
import { Empty, TableWrap } from "../ui";
import { plainPrice, usageShort } from "./catalogInfo";

/** Everything that costs money, biggest spend first. A row opens its details. */
export function SpendTable({ items, onOpen }: { items: CatalogItem[]; onOpen: (r: CatalogItem) => void }) {
  if (!items.length) return <Empty title="Nothing costs money yet" hint="Spend appears as soon as traffic flows through the gateway or a bill is imported." />;
  const total = items.reduce((s, r) => s + r.usage.usd, 0);
  const top = Math.max(...items.map((r) => r.usage.usd), 0);
  return (
    <>
      <ul className="divide-y divide-line md:hidden">
        {items.map((r) => (
          <li key={r.name}>
            <button type="button" onClick={() => onOpen(r)} className="block w-full px-4 py-3 text-left active:bg-raised">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 font-medium text-ink">{r.title || r.name}</span>
                <span className="tnum shrink-0 font-medium text-ink" title={exact(r.usage.usd, true)}>
                  {usdC(r.usage.usd)}
                </span>
              </div>
              <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-ink/[0.07]">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${top ? Math.max((r.usage.usd / top) * 100, r.usage.usd ? 1 : 0) : 0}%`, background: "var(--s1)" }}
                />
              </div>
              <div className="mt-1 text-[11px] text-muted">
                {plainPrice(r)} · {usageShort(r)}
              </div>
            </button>
          </li>
        ))}
      </ul>
      <div className="hidden md:block">
        <TableWrap>
          <table className="tbl min-w-[640px]">
            <thead>
              <tr>
                <th>What</th>
                <th>Price</th>
                <th className="w-[220px] text-right">Spend, 30 days</th>
                <th>Usage, 30 days</th>
                <th>Owner</th>
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.name} className="row-link" onClick={() => onOpen(r)} title="Details">
                  <td className="max-w-[280px]">
                    <div className="font-medium text-ink">{r.title || r.name}</div>
                    {r.description && <div className="line-clamp-1 text-[11px] text-muted">{r.description}</div>}
                  </td>
                  <td className="max-w-[220px] text-xs text-ink2">{plainPrice(r)}</td>
                  <td className="text-right">
                    <div className="tnum font-medium text-ink" title={exact(r.usage.usd, true)}>
                      {usdC(r.usage.usd)}
                      {total > 0 && r.usage.usd > 0 && (
                        <span className="ml-1.5 text-[11px] font-normal text-muted">{Math.round((r.usage.usd / total) * 100)}%</span>
                      )}
                    </div>
                    <div className="mt-1 ml-auto h-1.5 w-full max-w-[160px] overflow-hidden rounded-full bg-ink/[0.07]">
                      <div
                        className="h-full rounded-full"
                        style={{ width: `${top ? Math.max((r.usage.usd / top) * 100, r.usage.usd ? 1 : 0) : 0}%`, background: "var(--s1)" }}
                      />
                    </div>
                  </td>
                  <td className="text-xs text-ink2">{usageShort(r)}</td>
                  <td className="text-xs text-ink2">{r.owner || <span className="text-muted">unassigned</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      </div>
    </>
  );
}
