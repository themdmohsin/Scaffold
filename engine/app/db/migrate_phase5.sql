-- Phase 5 migration — SECURE ENVIRONMENT & CONTEXT: project environment schema
-- (metadata), access grants, and the server-side encryption keyring. Idempotent:
-- safe to run any number of times. The engine auto-applies this once per process
-- at startup (app/db/session.py), same pattern as migrate_day3/phase2/phase3.sql.
-- Additive only per the freeze rule in docs/SCHEMA.md — no renames, no drops.
--
-- SECURITY MODEL (read with docs/SCHEMA.md Phase 5 entry):
--   Layer 1 — METADATA: what variables exist (public.environment_variables).
--   Layer 2 — PERMISSIONS: who may retrieve what (public.environment_access).
--   Layer 3 — VALUES:   encrypted-at-rest ciphertext + the keyring
--                       (scaffold_secrets.* — NOT in `public`).
-- No plaintext secret value is EVER stored in any column, event payload, or log.
-- Secret VALUES are only returned by the dedicated retrieval endpoints — never by
-- GET /context, never in embeddings, never in AI prompts (see routes/environment.py).

-- ---------------------------------------------------------------------------
-- 1. Realtime publication exists (no-op when migrate_day3 already made it).
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    CREATE PUBLICATION supabase_realtime;
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 2. The secret-value layer lives in a NON-public schema (scaffold_secrets) so
--    a `SELECT *` against public.* can never touch ciphertext, and PostgREST /
--    anon clients — which are schema-scoped to public + storage by default —
--    have no path to it at all.
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS scaffold_secrets;

-- 2a. The encryption keyring. Supabase Vault was evaluated first (see
--     docs/SCHEMA.md Phase 5 entry): its column-encryption internals
--     (_crypto_aead_det_noncegen) require superuser privileges this project's
--     service role does not have, so we use the available pgcrypto PGP
--     functions (AES256) with a keyring of our own.
--
--     One row per key: name, key_id (the wrapping key: 32 random bytes stored
--     as 64 hex CHARACTERS — pgp_sym_encrypt/decrypt take a TEXT key, so the
--     keyring column is text, not bytea), created_at. The engine picks the
--     newest row by default; a specific key can be pinned with
--     SCAFFOLD_SECRET_KEYRING (new optional env var) so key rotation via a NEW
--     keyring row stays possible later without re-encrypting old rows (each
--     ciphertext row records its key_id).
CREATE TABLE IF NOT EXISTS scaffold_secrets.keyring (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name       TEXT NOT NULL UNIQUE,
  key_id     TEXT NOT NULL CHECK (char_length(key_id) = 64),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2a-repair. If an earlier revision of this migration created the keyring with
--     a bytea key_id (pgp_sym_encrypt rejects bytea keys — encryption failed
--     closed, no ciphertext rows were ever written that way), convert the
--     column to hex text in place. Idempotent: no-ops on the text layout.
DO $repair$
DECLARE
  coltype text;
BEGIN
  SELECT data_type INTO coltype FROM information_schema.columns
   WHERE table_schema = 'scaffold_secrets' AND table_name = 'keyring'
     AND column_name = 'key_id';
  IF coltype = 'bytea' THEN
    ALTER TABLE scaffold_secrets.keyring DROP CONSTRAINT IF EXISTS keyring_key_id_check;
    ALTER TABLE scaffold_secrets.keyring ALTER COLUMN key_id TYPE text USING encode(key_id, 'hex');
    ALTER TABLE scaffold_secrets.keyring ADD CONSTRAINT keyring_key_id_check CHECK (char_length(key_id) = 64);
  END IF;
END $repair$;

-- ---------------------------------------------------------------------------
-- 3. METADATA + PERMISSIONS (public schema — the Phase 5 tables).
--    These two tables carry NO secret material whatsoever: names, descriptions,
--    flags, timestamps, and grant rows. Safe to expose to the dashboard.
--    (environment_secrets below is created after these via a DO block, because
--    its FK needs the metadata tables to exist first.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS environment_variables (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  key         TEXT NOT NULL,
  description TEXT,
  required    BOOLEAN NOT NULL DEFAULT false,
  is_secret   BOOLEAN NOT NULL DEFAULT true,
  created_by  UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT env_var_key_shape CHECK (key ~ '^[A-Z][A-Z0-9_]*$'),
  CONSTRAINT env_var_project_key_unique UNIQUE (project_id, key)
);

CREATE TABLE IF NOT EXISTS environment_access (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  environment_variable_id UUID NOT NULL REFERENCES environment_variables(id) ON DELETE CASCADE,
  user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  granted_by              UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT env_access_unique UNIQUE (environment_variable_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_env_vars_project     ON environment_variables(project_id);
CREATE INDEX IF NOT EXISTS idx_env_vars_project_key ON environment_variables(project_id, key);
CREATE INDEX IF NOT EXISTS idx_env_access_var       ON environment_access(environment_variable_id);
CREATE INDEX IF NOT EXISTS idx_env_access_user      ON environment_access(user_id);

-- Realtime: both metadata tables publish (dashboard live status updates).
-- Each ADD TABLE is wrapped in its own DO block: a re-run raises duplicate_object
-- and must not abort the remaining statements (the migration is one script).
DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE public.environment_variables;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE public.environment_access;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------------------------------------------------------------------------
-- 4. The ciphertext store (created AFTER the metadata tables — its FK needs
--    them). `encrypted` is ALWAYS pgp_sym_encrypt(value, key) performed
--    SERVER-SIDE in one statement (see services/secret_store.py): the plaintext
--    is bound as a bind parameter and never written to a column, a log, or an
--    event. `key_id` records WHICH keyring row wrapped it so a future key
--    rotation can lazily re-encrypt.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  CREATE TABLE scaffold_secrets.environment_secrets (
    environment_variable_id UUID PRIMARY KEY REFERENCES public.environment_variables(id) ON DELETE CASCADE,
    encrypted               BYTEA NOT NULL,
    key_id                  UUID NOT NULL REFERENCES scaffold_secrets.keyring(id),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
  );
EXCEPTION WHEN duplicate_table THEN NULL; END $$;

-- ---------------------------------------------------------------------------
-- 5. Lock the value layer down: anon / authenticated PostgREST roles have no
--    grants on this schema at all; only the engine's service connection may
--    touch ciphertext (and it only ever ships values to AUTHORIZED members —
--    see routes/environment.py). Wrap each REVOKE: the roles may not exist on
--    non-Supabase rigs (local Postgres dev), and that must not abort the rest.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  REVOKE ALL ON SCHEMA scaffold_secrets FROM anon;
EXCEPTION WHEN undefined_object THEN NULL; END $$;
DO $$
BEGIN
  REVOKE ALL ON SCHEMA scaffold_secrets FROM authenticated;
EXCEPTION WHEN undefined_object THEN NULL; END $$;
DO $$
BEGIN
  REVOKE ALL ON scaffold_secrets.keyring, scaffold_secrets.environment_secrets FROM anon;
EXCEPTION WHEN undefined_object THEN NULL; END $$;
DO $$
BEGIN
  REVOKE ALL ON scaffold_secrets.keyring, scaffold_secrets.environment_secrets FROM authenticated;
EXCEPTION WHEN undefined_object THEN NULL; END $$;

-- ---------------------------------------------------------------------------
-- 6. Keyring bootstrap — run LAST, exactly once per fresh database.
--    SCAFFOLD_SECRET_KEYRING (optional engine env var, NOT stored here) only
--    NAMES the active keyring row for the engine (secret_store pins its
--    lookups by that name). The key material is ALWAYS generated inside
--    Postgres (gen_random_bytes, pgcrypto): it never transits the engine
--    process, env files, or logs at all.
--       • SET      -> keyring row `<name>` is created (random key, server-side)
--                     so multiple named keys can coexist for future rotation.
--       • NOT SET  -> a row named `default` is created.
--    Handled here (DO block) rather than in Python so the key never appears in
--    an engine-side traceback, and so a stale Python process can't desync the
--    keyring. Re-runs are no-ops when the keyring row already exists.
-- ---------------------------------------------------------------------------
DO $bootstrap$
DECLARE
  kname text;
BEGIN
  BEGIN
    kname := current_setting('scaffold.secret_keyring', true);
  EXCEPTION WHEN OTHERS THEN
    kname := NULL;
  END;
  IF kname IS NULL OR kname = '' THEN
    kname := 'default';
  END IF;

  INSERT INTO scaffold_secrets.keyring (name, key_id)
  SELECT kname, encode(gen_random_bytes(32), 'hex')
  WHERE NOT EXISTS (SELECT 1 FROM scaffold_secrets.keyring WHERE name = kname);
END $bootstrap$;
