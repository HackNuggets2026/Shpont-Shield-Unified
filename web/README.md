# Shpont Shield web

The admin console and employee portal: a Vite + React 18 + TypeScript SPA (Tailwind, Recharts, TanStack Query, React Router).

## Develop

```sh
# a gateway with 30 days of history (from the repo root)
python -m seed --data-dir /tmp/shield-seeded --days 30 --seed 42
python -m controllayer --data-dir /tmp/shield-seeded --port 8790

cd web
npm install
SHIELD_GATEWAY=http://127.0.0.1:8790 npm run dev   # http://localhost:5173
```

The dev server proxies `/api` and `/v1` to `SHIELD_GATEWAY` (the default is `http://127.0.0.1:8787`).

## Build

```sh
npm run build        # tsc -b (strict) + vite build -> web/dist
```

The gateway serves `web/dist` by itself. It serves `index.html` at `/` and at every non-API path, so client-side routes like `/console/people/frank` work on reload, and it serves `/assets/*` from `web/dist/assets`. Override the location with `ACL_WEB_DIST`. Restart the gateway after the first build: it checks for `dist` at startup.

## Auth

The login screen takes one secret:

1. The SPA calls `GET /api/session` with `x-admin-token: <secret>`.
2. If that returns 401, it retries with `Authorization: Bearer <secret>`.
3. `role: "admin"` lands in `/console`. `role: "employee"` lands in `/portal`.

The credentials and session are kept in `localStorage` (`shield.auth`; every access is wrapped in try/catch). Admin requests send `x-admin-token`, plus `x-admin-user` when the optional name field was filled in. That name is recorded as the actor of admin actions and shown to employees. Employee requests send `Authorization: Bearer`. Any 401 signs the user out.

Demo secrets: `demo-admin-token`, `dev-alice-key`, `dev-frank-key` (quarantined), `fin-bob-key`, `intern-key`, and the other `*-key` entries in `policy.yaml`.

## Layout

| Path | What |
|---|---|
| `src/api.ts` | typed API client and response types |
| `src/auth.tsx` | session context (login, logout, storage) |
| `src/components/` | shell and nav, UI primitives, pills, charts, activity feed, dialogs (`ReasonDialog`), leases, grants, admin log |
| `src/lib/` | formatting (USD precision for sub-cent costs, tokens k/M, relative times), theme, Claude Code productivity |
| `src/pages/console/` | Overview, Workflows, People, Person, Security, Incident, Resources, Requests |
| `src/pages/portal/` | Home, Menu, Access, Activity, Privacy |

Theme: dark and light follow `prefers-color-scheme`. The theme button in the sidebar cycles system, dark and light. Every action that needs a reason goes through `ReasonDialog`, which shows the server's error inline (for example a 422 from a rejected policy write).

API gaps and workarounds are listed in [API_NOTES.md](API_NOTES.md).
