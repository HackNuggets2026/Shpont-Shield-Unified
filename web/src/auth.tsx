import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { probeSession, setCredentials, setUnauthorizedHandler, type Credentials, type Session } from "./api";

interface Stored {
  creds: Credentials;
  session: Session;
}

const KEY = "shield.auth";

function load(): Stored | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as Stored;
    return v?.creds?.secret && v?.session?.role ? v : null;
  } catch {
    return null;
  }
}

function save(v: Stored | null) {
  try {
    if (v) localStorage.setItem(KEY, JSON.stringify(v));
    else localStorage.removeItem(KEY);
  } catch {
    /* storage unavailable: the session lives only in memory */
  }
}

interface AuthState {
  session: Session | null;
  creds: Credentials | null;
  login: (secret: string, adminUser?: string) => Promise<Session>;
  logout: () => void;
}

const Ctx = createContext<AuthState | null>(null);

const initial = load();
setCredentials(initial?.creds ?? null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<Stored | null>(initial);
  const qc = useQueryClient();

  const logout = useCallback(() => {
    setCredentials(null);
    save(null);
    setState(null);
    qc.clear();
  }, [qc]);

  useEffect(() => {
    setUnauthorizedHandler(logout);
    return () => setUnauthorizedHandler(null);
  }, [logout]);

  const login = useCallback(
    async (secret: string, adminUser?: string) => {
      const { session, creds } = await probeSession(secret.trim(), adminUser?.trim() || undefined);
      const v = { session, creds };
      qc.clear();
      setCredentials(creds);
      save(v);
      setState(v);
      return session;
    },
    [qc],
  );

  const value = useMemo<AuthState>(
    () => ({ session: state?.session ?? null, creds: state?.creds ?? null, login, logout }),
    [state, login, logout],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
