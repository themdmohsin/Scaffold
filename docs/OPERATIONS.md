# OPERATIONS.md — Running Scaffold in production

> Companion to `docs/DEPLOYMENT.md` (how to deploy) and `docs/SCHEMA.md` (what the
> database looks like). Everything here is written for ONE shared engine + ONE Supabase
> project, the deployment shape this repo targets.

## 1. Daily checks

| Surface | Healthy | Degraded |
| --- | --- | --- |
| `GET /health` | `200 {"status":"ok"}` | process down |
| `GET /ready` | `200 {"status":"ready","checks":{"database":"ok","migrations":"ok"}}` | `503` with `database: "unavailable"` or `migrations: "pending:N"` |
| `python -m app.scripts.migrate --status` | no pending, no checksum mismatches | pending list / checksum warnings |
| Structured logs | JSON lines with `request_id` | `rate_limit_exceeded` bursts, `503` on auth routes |

`/health` is a pure liveness probe (never touches the DB). `/ready` is the readiness
probe and is what a load balancer / Fly health check should gate traffic on. Both are
unauthenticated by design (orchestrators cannot carry credentials) and leak no config.

## 2. Backups

Supabase takes automatic daily backups on paid plans; do not rely on that alone for a
demo you care about. Two levels:

**Full logical backup (schema + data), run from any machine with `pg_dump` 16+:**

```bash
# from the Supabase connection string (pooler or direct)
pg_dump "postgresql://postgres.<ref>:<pw>@aws-0-<region>.pooler.supabase.com:5432/postgres" \
  --format=custom --no-owner --no-privileges \
  --file=scaffold-$(date +%Y%m%d-%H%M).dump
```

Store it somewhere the team can reach (object storage / password manager). A backup that
has never been restored is not a backup — do the drill in §3 once per project.

**Schema-only snapshot** (cheap, useful before migrations):

```bash
pg_dump "$DATABASE_URL" --schema-only --no-owner > schema-snapshot.sql
```

## 3. Restore drill / recovery

```bash
# 1. Create a NEW empty Supabase project (or a plain Postgres with pgvector + pgcrypto).
# 2. Restore:
pg_restore --no-owner --no-privileges --dbname="$NEW_DATABASE_URL" scaffold-YYYYMMDD-HHMM.dump
# 3. Re-apply anything the dump missed (extensions live outside the dump's schema scope
#    on some plans), then verify the migration bookkeeping:
cd engine
python -m app.scripts.bootstrap_db --status     # verify-only: tables, keyring, publication
python -m app.scripts.migrate --status          # should report no pending
# 4. Point the engine at the new database (fly secrets set DATABASE_URL=...), deploy,
#    confirm GET /ready -> 200.
```

Caveats:

- Restoring into an existing database can hit duplicate-key errors on rows that survived.
  Prefer a fresh database, or `pg_restore --clean --if-exists`.
- The `schema_migrations` table is included in the dump; if you restore data into a
  database whose schema was created by migrations, re-run `migrate --status` to confirm
  the recorded checksums match the files.
- `scaffold_secrets` (ciphertext + keyring) lives in the `scaffold_secrets` schema and IS
  included in a full dump. Without the keyring rows the ciphertext is unreadable — never
  restore a partial dump that omits that schema.

## 4. Secret-key rotation (Phase 5 secret store)

The secret store wraps every environment-variable value with a key from the
`scaffold_secrets.keyring` table (see `docs/SCHEMA.md` → `scaffold_secrets`). Each
ciphertext row records the `key_id` that wrapped it. Rotation procedure:

1. **Add a new keyring row** (do not delete the old one yet):

   ```sql
   -- run as the service role (SQL editor or psql)
   INSERT INTO scaffold_secrets.keyring (name, key)
   VALUES ('rotated-2026-10', encode(gen_random_bytes(32), 'hex'));
   ```

   The migration bootstrapped a row named `default`; any new name works.

2. **Pin the engine to the new key** so new writes use it:
   `fly secrets set SCAFFOLD_SECRET_KEYRING=rotated-2026-10` (or set it in `.env`).
   If unset, the newest keyring row is used — setting it explicitly removes ambiguity.

3. **Re-wrap existing values.** There is no bulk re-encrypt endpoint yet. Two options:

   - Re-set each secret through the API (owner-only): `POST /projects/:id/environment/variables`
     with the same key and value — the write re-wraps with the active key.
   - SQL re-wrap is possible because each row stores its `key_id`, but do it only with a
     tested script and a fresh backup; the plaintext never leaves the engine otherwise.

4. **Retire the old key** only after every ciphertext row references the new `key_id`:

   ```sql
   SELECT DISTINCT key_id FROM scaffold_secrets.secret_values;  -- or the ciphertext table
   DELETE FROM scaffold_secrets.keyring WHERE name = 'default';
   ```

   If you delete a key that still wraps rows, those values become unreadable — the engine
   will surface errors on pull/request for the affected variables.

**Also rotate when a teammate leaves:** revoke their PATs (`DELETE /auth/tokens/:id`),
remove their membership (`DELETE /projects/:id/members/:member_id` — takes effect
immediately), and rotate any secret value they could read.

## 5. Rate limiting — what it does and does not cover

The engine ships a deterministic in-process limiter (sliding 60-second window per
`scope:client-ip:credential-fingerprint`):

- scope `auth` — `/auth/*`, `POST /projects/join`, `POST .../invite` (default 120/min)
- scope `reason` — `POST /projects/:id/reason` (default 30/min — the paid LLM call)
- scope `secret` — environment request/pull + value mutations (default 240/min)

Limits are per engine replica. With more than one replica (Fly `min_machines_running`,
scale count > 1) each instance enforces its own budget — for a multi-replica deployment
add an edge limiter (Fly/Railway do not rate limit by default; Cloudflare or an API
gateway does). For the team-sized deployment this repo targets, one replica is correct.

Denied requests return `429` with `Retry-After`, `X-RateLimit-Limit`,
`X-RateLimit-Remaining`. `SCAFFOLD_RATE_LIMIT_ENABLED=0` disables the limiter (load
tests only).

## 6. Migrations — operational rules

- The manifest lives in `engine/app/db/migrations.py`; applied history is recorded in
  `schema_migrations` (version, name, checksum, applied_at, execution_ms,
  skipped_statements).
- **Forward-only.** Never edit an applied file; the runner reports a checksum mismatch as
  a warning and does NOT re-run it. Add a new migration instead.
- Startup applies pending migrations automatically (fail-open: a broken DB does not stop
  `/health`; `/ready` reports `503` until migrations are current).
- `migrate --check` exits non-zero when anything is pending — use it as a CI gate.
- A `tolerant` migration (currently `0006`) applies statement-by-statement and records how
  many statements were skipped. On a real Supabase project the count should be `0`; a
  non-zero count on Supabase means the migration needs attention:
  `python -m app.scripts.migrate --retry-skipped`.

## 7. Incident runbook

**`/ready` is 503.**

1. Read the response: `database: "unavailable"` → connection problem (check Supabase status,
   connection string, IP restrictions). `migrations: "pending:N"` → run
   `python -m app.scripts.migrate` and re-check.
2. Structured logs carry the reason (`scaffold.startup`, `scaffold.migrations` loggers).

**Engine returns 503 on every authenticated route.** The engine cannot verify tokens: `SUPABASE_URL` is unset or its JWKS (`/auth/v1/.well-known/jwks.json`) is unreachable and nothing is cached (ES256), or (legacy HS256) `SUPABASE_JWT_SECRET` is unset or
wrong — fail-closed by design. Set it and redeploy.

**Invite links stopped working.** `GITHUB_WEBHOOK_SECRET` also signs invite codes. If it
was rotated, existing links are invalid — create fresh invites.

**A teammate cannot join / sees 403.** Check `project_members` (active row? role?) via
`GET /projects/:id/members` with an owner PAT. Removal is immediate; a removed member's
JWT stops working on the next request.

**Suspected credential leak.** Revoke the PAT (`DELETE /auth/tokens/:id`), rotate the
Supabase service key + JWT secret (Supabase Dashboard → Settings → API → rotate), rotate
the team LLM key, then redeploy with the new secrets. Audit `events` for the affected
window. Never paste raw credentials into logs/issues — the engine's redaction keeps them
out of its own logs, but that is not a substitute for rotation.

## 8. Logs and observability

- Default format is JSON lines (`SCAFFOLD_LOG_FORMAT=json`), one object per request:
  `ts, level, logger, msg, request_id, http_method, path, status, duration_ms, client`.
- Every response carries `X-Request-Id` (echoed from the request when safe, generated
  otherwise) — quote it when reporting a problem; grep the logs for it.
- Redaction runs on every handler: `scaffold_…` PATs, `Bearer …`, JWT-shaped strings,
  `password=…`-style pairs, and DSN passwords become `***` before the line is written.
- `uvicorn.access` is disabled in favor of the engine's own access line (no query
  strings, no bodies, no header values are ever logged).
- Fly: `fly logs`; Railway: the service's Deploy logs. Pipe them to a collector if you
  want retention beyond the provider default.
