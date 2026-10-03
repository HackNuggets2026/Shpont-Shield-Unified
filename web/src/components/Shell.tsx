import { useEffect, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth";
import { useTheme } from "../lib/theme";
import { IconBars, IconLogout, IconMonitor, IconMoon, IconShield, IconSun, IconX } from "./icons";
import { cx } from "./ui";

export interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  end?: boolean;
  badge?: number | null;
  badgeTone?: "bad" | "warn";
  /** Other path prefixes that should light this item up (e.g. people pages under Organization). */
  also?: string[];
}

function Brand({ area }: { area: string }) {
  return (
    <div className="flex items-center gap-2.5 px-1">
      <div className="bg-brand flex h-8 w-8 items-center justify-center rounded-xl text-white shadow-sm">
        <IconShield size={18} />
      </div>
      <div className="leading-tight">
        <div className="text-sm font-semibold text-ink">Shpont Shield</div>
        <div className="text-[11px] text-muted">{area}</div>
      </div>
    </div>
  );
}

function Nav({ items, onNavigate }: { items: NavItem[]; onNavigate?: () => void }) {
  const { pathname } = useLocation();
  return (
    <nav className="space-y-0.5">
      {items.map((it) => (
        <NavLink
          key={it.to}
          to={it.to}
          end={it.end}
          onClick={onNavigate}
          className={({ isActive }) =>
            cx(
              "flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm font-medium transition-colors",
              isActive || it.also?.some((p) => pathname === p || pathname.startsWith(p + "/"))
                ? "bg-ink text-page shadow-sm"
                : "text-ink2 hover:bg-raised hover:text-ink",
            )
          }
        >
          <span className="shrink-0">{it.icon}</span>
          <span className="flex-1 truncate">{it.label}</span>
          {!!it.badge && (
            <span
              className={cx(
                "tnum rounded-full px-1.5 text-[11px] font-semibold",
                it.badgeTone === "bad" ? "bg-bad/15 text-bad" : "bg-warn/15 text-warn",
              )}
            >
              {it.badge >= 10000 ? `${Math.round(it.badge / 1000)}k` : it.badge.toLocaleString("en-US")}
            </span>
          )}
        </NavLink>
      ))}
    </nav>
  );
}

function ThemeButton() {
  const [t, cycle] = useTheme();
  const icon = t === "dark" ? <IconMoon /> : t === "light" ? <IconSun /> : <IconMonitor />;
  return (
    <button
      type="button"
      onClick={cycle}
      className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-muted hover:bg-raised hover:text-ink"
      title="Theme: system → dark → light"
    >
      {icon}
      <span className="capitalize">{t}</span>
    </button>
  );
}

function UserBox() {
  const { session, creds, logout } = useAuth();
  if (!session) return null;
  const name = session.role === "admin" ? creds?.adminUser || session.name : session.principal;
  const sub = session.role === "admin" ? "Administrator" : `${session.team} · ${session.job_role}`;
  return (
    <div className="flex items-center gap-2 rounded-xl border border-line/70 bg-raised/50 p-2">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent/15 text-xs font-semibold uppercase text-accent">
        {name.slice(0, 2)}
      </div>
      <div className="min-w-0 flex-1 leading-tight">
        <div className="truncate text-sm font-medium text-ink">{name}</div>
        <div className="truncate text-[11px] text-muted">{sub}</div>
      </div>
      <button type="button" onClick={logout} className="rounded p-1.5 text-muted hover:bg-raised hover:text-ink" title="Sign out" aria-label="Sign out">
        <IconLogout />
      </button>
    </div>
  );
}

export function Shell({ area, items, search }: { area: string; items: NavItem[]; search?: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);

  const side = (
    <div className="flex h-full flex-col gap-5 p-3">
      <Brand area={area} />
      {search && <div>{search(() => setOpen(false))}</div>}
      <div className="flex-1 overflow-y-auto">
        <Nav items={items} onNavigate={() => setOpen(false)} />
      </div>
      <div className="space-y-2">
        <ThemeButton />
        <UserBox />
      </div>
    </div>
  );

  return (
    <div className="min-h-screen bg-page">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 border-r border-line/70 bg-panel/80 backdrop-blur-xl lg:block">{side}</aside>

      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-line/70 bg-panel/85 px-4 py-2.5 backdrop-blur-xl lg:hidden">
        <Brand area={area} />
        <button type="button" onClick={() => setOpen(true)} className="rounded-md p-2 text-ink2 hover:bg-raised" aria-label="Open menu">
          <IconBars size={18} />
        </button>
      </header>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-72 max-w-[85vw] border-r border-line bg-panel shadow-2xl">
            <button type="button" onClick={() => setOpen(false)} className="absolute right-2 top-3 rounded p-1.5 text-muted hover:bg-raised" aria-label="Close menu">
              <IconX />
            </button>
            {side}
          </aside>
        </div>
      )}

      <main className="lg:pl-60">
        <div className="mx-auto max-w-[1400px] px-4 py-5 sm:px-8 lg:py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
