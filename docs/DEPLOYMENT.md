# DEPLOYMENT.md — Running ONE shared Scaffold engine for the team

> The goal: every teammate's dashboard + OpenCode plugin points at ONE engine URL
> (not `localhost:8000`). Recommended path: **Fly.io** (engine) + **Supabase** (database,
> already in use) + the dashboard served as a static site (Fly.io or Vercel/Netlify).
> Railway notes are at the bottom — the config is equivalent.
>
> Nothing here changes frozen contracts. New Day-9 env vars are listed in the table below
> and documented in `engine/.env.example`.

## 0. What you are deploying

```
teammate laptop ──HTTPS──> dashboard (static)  ──HTTPS──> engine (Fly.io) ──TLS──> Supabase Postgres
teammate laptop ──HTTPS──> OpenCode plugin (SCAFFOLD_ENGINE_URL + SCAFFOLD_TOKEN) ──┘
```

- **Engine** — `engine/` (FastAPI). One instance is enough for a team; it is stateless
  apart from the in-process rate limiter (see §6).
- **Database** — your existing Supabase project. The engine applies migrations at boot;
  `python -m app.scripts.bootstrap_db` does a from-zero setup (extensions, schema,
  migrations, Realtime publication).
- **Dashboard** — `dashboard/` (React + Vite). Built with `VITE_ENGINE_URL` pointing at
  the deployed engine, then served as static files.

## 1. Environment variables (complete list)

Set these as Fly secrets (`fly secrets set ...`), Railway variables, or in `engine/.env`
for local runs. Never commit real values.

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | yes | Supabase Postgres connection string. Use the **pooler** string (`...pooler.supabase.com:5432/postgres`). |
| `SUPABASE_URL` | yes | `https://<ref>.supabase.co` — JWT issuer validation + docs links. |
| `SUPABASE_SERVICE_KEY` | yes | Service-role key; the engine bypasses RLS with it. |
| `SUPABASE_JWT_SECRET` | yes | Supabase Dashboard → Settings → API → JWT Secret. **Without it every authenticated route 503s (fail closed).** |
| `SCAFFOLD_TEAM_LLM_KEY` | for `/reason` | Pays for the engine's own reasoning/embedding calls only — never a developer's coding session. |
| `GITHUB_WEBHOOK_SECRET` | for webhooks + invites | GitHub webhook HMAC secret; also signs invite codes. |
| `GITHUB_TOKEN` | optional | Auto-filing conflict issues. |
| `SCAFFOLD_CORS_ORIGINS` | yes in prod | Comma-separated dashboard origin(s), e.g. `https://scaffold-dashboard.fly.dev`. Empty = wildcard (dev only) and is **fatal in production**. |
| `SCAFFOLD_BOOTSTRAP_ACCOUNT_IDS` | no | DEV/SEED ONLY. Auto-promotes listed Supabase user ids to owner. **Fatal in production.** |
| `SCAFFOLD_ENV` | yes (prod) | `production` — makes startup config validation fatal with actionable messages. |
| `SCAFFOLD_LOG_FORMAT` | no | `json` (default) or `text`. |
| `SCAFFOLD_LOG_LEVEL` | no | Default `INFO`. |
| `SCAFFOLD_RATE_LIMIT_ENABLED` | no | Default `1`. Set `0` to disable (e.g. load tests). |
| `SCAFFOLD_RATE_LIMIT_AUTH_PER_MINUTE` | no | Default `120` — `/auth/*`, `POST /projects/join`, `POST .../invite`. |
| `SCAFFOLD_RATE_LIMIT_REASON_PER_MINUTE` | no | Default `30` — `POST /projects/:id/reason` (the paid call). |
| `SCAFFOLD_RATE_LIMIT_SECRET_PER_MINUTE` | no | Default `240` — environment value endpoints. |
| `SCAFFOLD_TRUST_PROXY` | yes behind a proxy | `1` on Fly/Railway so rate-limit keys and access logs see the real client IP. |
| `SCAFFOLD_DEFAULT_PROJECT_ID`, `SCAFFOLD_LLM_MODEL`, `SCAFFOLD_EMBED_MODEL`, `SCAFFOLD_GITHUB_REPO`, `SCAFFOLD_SECRET_KEYRING` | no | Pre-existing optional knobs (see `.env.example`). |

Dashboard build-time vars (Vite — baked into the static build):

| Variable | Value |
| --- | --- |
| `VITE_ENGINE_URL` | `https://<engine-app>.fly.dev` |
| `VITE_SUPABASE_URL` | `https://<ref>.supabase.co` |
| `VITE_SUPABASE_ANON_KEY` | Supabase anon key |

Plugin (each teammate's machine): `SCAFFOLD_ENGINE_URL=https://<engine-app>.fly.dev`
and `SCAFFOLD_TOKEN=scaffold_…` (a PAT minted via `POST /auth/tokens`).

## 2. Bootstrap the database (once, from zero)

Run against a **new** Supabase project (or to repair an existing one):

```bash
cd engine
cp .env.example .env   # fill DATABASE_URL, SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_JWT_SECRET
python -m app.scripts.bootstrap_db
```

`bootstrap_db` is idempotent and:

1. verifies the connection and creates the `pgcrypto` + `vector` extensions
   (with exact Supabase fix hints if the `vector` extension is unavailable),
2. applies every migration in order (`0001`…`0006`) and records them in `schema_migrations`,
3. adds the Realtime tables to the `supabase_realtime` publication,
4. verifies the critical tables and the secret-store keyring exist,
5. prints the next steps (set `SUPABASE_JWT_SECRET`, sign in, mint a PAT).

Useful flags: `--lenient` (plain Postgres rigs — skips Supabase-only RLS statements
instead of failing), `--status` (verify only, apply nothing), `--retry-skipped`
(re-apply tolerant migrations that previously skipped statements).

Routine checks:

```bash
python -m app.scripts.migrate --status    # what is applied / pending / checksum drift
python -m app.scripts.migrate --check     # CI gate: exit 1 when anything is pending
```

## 3. Deploy the engine on Fly.io (recommended)

Prerequisites: `flyctl` installed and `fly auth login` done.

```bash
cd engine
fly launch --no-deploy --copy-config          # uses engine/fly.toml (app name: edit it first)
fly secrets set \
  DATABASE_URL="postgresql://postgres.<ref>:<pw>@aws-0-<region>.pooler.supabase.com:5432/postgres" \
  SUPABASE_URL="https://<ref>.supabase.co" \
  SUPABASE_SERVICE_KEY="<service-role-key>" \
  SUPABASE_JWT_SECRET="<jwt-secret>" \
  GITHUB_WEBHOOK_SECRET="<webhook-secret>" \
  SCAFFOLD_TEAM_LLM_KEY="<llm-key>" \
  SCAFFOLD_CORS_ORIGINS="https://<dashboard-host>"
fly deploy
fly open /health                               # {"status":"ok"}
curl -s https://<app>.fly.dev/ready            # {"status":"ready", ...}
```

`engine/fly.toml` sets `SCAFFOLD_ENV=production` and `SCAFFOLD_TRUST_PROXY=1`, keeps one
machine always warm (`min_machines_running = 1`), and health-checks `/health`.

**One-command path** after the first deploy: `fly deploy` (migrations apply at boot;
`/ready` stays 503 until they are current, so traffic is never routed early).

**Custom domain / HTTPS**: `fly certs add scaffold-api.example.com`, add the shown DNS
records, then update `SCAFFOLD_CORS_ORIGINS` and every teammate's `VITE_ENGINE_URL` /
`SCAFFOLD_ENGINE_URL`. Fly terminates TLS automatically.

## 4. Deploy the dashboard

The dashboard is static. Build with the engine URL baked in, then host it:

```bash
cd dashboard
VITE_ENGINE_URL=https://<engine-app>.fly.dev npm run build   # -> dashboard/dist
```

- **Fly.io**: `fly launch --no-deploy --copy-config` in `dashboard/` (its `fly.toml`
  serves the container built by `dashboard/Dockerfile`), `fly deploy`.
- **Vercel / Netlify / any static host**: point it at `dashboard/` with build command
  `npm run build`, output `dist/`, and the same `VITE_*` env vars. Add the host's origin
  to `SCAFFOLD_CORS_ORIGINS` on the engine.

## 5. Docker / docker-compose (local, optional)

`docker-compose.yml` at the repo root runs the engine + dashboard together:

```bash
docker compose up -d engine            # engine only, against the Supabase in engine/.env
docker compose --profile local-db up -d  # + a throwaway pgvector Postgres on :5433
```

Notes:

- **Dashboard build vars come from the repo-root `.env`** (see `.env.example`), not
  `engine/.env` and not `dashboard/.env` (excluded from the Docker context). Compose passes
  `VITE_ENGINE_URL` / `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY` as build args; changing
  them requires `docker compose up -d --build dashboard`. Without the two Supabase vars the
  dashboard shows a "sign-in not configured" error.
- **The engine needs `SUPABASE_JWT_SECRET` in `engine/.env`** or every JWT-authenticated
  call (i.e. every dashboard call) returns 503. Restart: `docker compose up -d engine`.
- Dashboard flow: sign in (email/password or GitHub) -> project picker (`GET /projects`,
  create, or join with an invite code) -> control center. Nobody pastes a project UUID.
- `engine/.env` is mounted when present (`required: false`); without it, pass variables
  via `environment:` or the shell.
- The `local-db` profile runs `pgvector/pgvector:pg16` (user/password/db `scaffold`).
  Point `DATABASE_URL` at `postgresql+psycopg://scaffold:scaffold@postgres:5432/scaffold`
  and run `python -m app.scripts.bootstrap_db --lenient` (plain Postgres has no Supabase
  `anon`/`authenticated` roles, so RLS statements are skipped by design).
- Compose file uses `env_file` with `required: false` — needs Compose ≥ 2.24.

## 6. GitHub webhook (deployed URL)

In GitHub → repo → Settings → Webhooks → Add webhook:

- **Payload URL**: `https://<engine-app>.fly.dev/projects/<project-id>/github-webhook`
- **Content type**: `application/json`
- **Secret**: the same value as `GITHUB_WEBHOOK_SECRET`
- **Events**: `push` (and `pull_request` if you use it)

The endpoint is HMAC-verified and unauthenticated by design (GitHub cannot carry a user
credential); everything else requires `Authorization: Bearer`. To find `<project-id>`,
call `GET /projects` with a PAT.

## 7. Railway notes (alternative)

Equivalent config — create a project from the `engine/` directory (Dockerfile build),
set the same variables in **Variables**, and Railway terminates TLS at the edge:

- Set `SCAFFOLD_ENV=production`, `SCAFFOLD_TRUST_PROXY=1`, and the full variable table.
- Railway provides `PORT`; the engine's Dockerfile listens on 8000 — set the service's
  port or add `PORT`-aware start if you customize the command.
- Health check path: `/health` (Railway also supports `/ready` if you prefer gating).
- Custom domain: Settings → Networking → Custom Domain.

## 8. Verification after deploy

```bash
curl -s https://<engine-app>.fly.dev/health   # {"status":"ok"}
curl -s https://<engine-app>.fly.dev/ready    # 200 {"status":"ready"} — 503 until DB+migrations are good
```

Then, on one teammate machine: sign in through the dashboard, `POST /auth/tokens` to mint
a PAT, set `SCAFFOLD_ENGINE_URL` + `SCAFFOLD_TOKEN` for the plugin, and confirm
`GET /projects` returns the shared project. From `engine/`:

```bash
python -m tests.test_deploy   # deployment hardening suite (84 checks)
```

## 9. Rollback

`fly releases` → `fly deploy --image <previous-image>` (or `fly releases rollback`).
Migrations are forward-only; the previous image still boots because the runner only
applies pending versions and never re-runs applied ones. See `docs/OPERATIONS.md` for
backup/restore before risky changes.
