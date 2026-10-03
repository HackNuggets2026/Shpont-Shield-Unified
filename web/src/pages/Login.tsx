import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { ApiError } from "../api";
import { useAuth } from "../auth";
import { Button, ErrorBox, Field } from "../components/ui";
import { IconShield } from "../components/icons";

const DEMO = [
  { label: "Admin", value: "demo-admin-token" },
  { label: "alice (dev)", value: "dev-alice-key" },
  { label: "frank (quarantined)", value: "dev-frank-key" },
  { label: "bob (finance)", value: "fin-bob-key" },
  { label: "carol (intern)", value: "intern-key" },
];

export function Login() {
  const { session, login } = useAuth();
  const nav = useNavigate();
  const [secret, setSecret] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (session) return <Navigate to={session.role === "admin" ? "/console" : "/portal"} replace />;

  const submit = async (value = secret) => {
    if (!value.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const s = await login(value, name);
      nav(s.role === "admin" ? "/console" : "/portal", { replace: true });
    } catch (e) {
      setError(e instanceof ApiError && e.status === 401 ? new Error("That is neither the admin token nor a known API key.") : e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-page px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-accent text-white shadow-lg shadow-accent/20">
            <IconShield size={24} />
          </div>
          <h1 className="text-lg font-semibold text-ink">Shpont Shield</h1>
          <p className="mt-1 text-sm text-muted">One control plane for your company's AI usage.</p>
        </div>
        <form
          className="space-y-4 rounded-2xl border border-line bg-panel p-5 shadow-sm"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <Field label="Admin token or your API key" hint="Admins land in the console; employees see their own portal.">
            <input
              className="input font-mono"
              type="password"
              autoComplete="current-password"
              autoFocus
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              placeholder="e.g. dev-alice-key"
            />
          </Field>
          <Field label="Your name (admins, optional)" hint="Recorded as the actor of admin actions and shown to employees.">
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Maria (SecOps)" autoComplete="name" />
          </Field>
          {error != null && <ErrorBox error={error} compact />}
          <Button variant="primary" className="w-full" type="submit" disabled={busy || !secret.trim()}>
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>
        <div className="mt-4 text-center text-[11px] text-muted">
          <div className="mb-1.5">Demo credentials</div>
          <div className="flex flex-wrap justify-center gap-1.5">
            {DEMO.map((d) => (
              <button
                key={d.value}
                type="button"
                onClick={() => {
                  setSecret(d.value);
                  void submit(d.value);
                }}
                className="rounded-full border border-line bg-panel px-2.5 py-1 text-ink2 hover:border-accent/50 hover:text-ink"
              >
                {d.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
