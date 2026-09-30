-- Phase 3 migration — team collaboration: agent identity, membership status,
-- project ownership. Idempotent: safe to run any number of times. The engine
-- auto-applies this once per process at startup (app/db/session.py), same
-- pattern as migrate_day3.sql / migrate_phase2.sql. Matches docs/SCHEMA.md
-- (Phase 3 additions). Additive only per the freeze rule in docs/SCHEMA.md —
-- no renames, no drops, every new column nullable or defaulted.

-- 1. users: agent identity + membership bookkeeping.
--    `kind` distinguishes a human developer from an AI agent participant
--    without inventing a second roster table (repo rule: no duplicate systems).
--    `role` stays the free-text column Day 5 already writes to (e.g. "backend",
--    "frontend") — this migration does not constrain it, so existing rows and
--    the frozen invite/join contract (test_day5) are untouched.
ALTER TABLE users ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'developer';
ALTER TABLE users ADD COLUMN IF NOT EXISTS agent_provider TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS agent_model TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS agent_session_id TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS membership_status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE users ADD COLUMN IF NOT EXISTS joined_at TIMESTAMPTZ NOT NULL DEFAULT now();

DO $$
BEGIN
  BEGIN
    ALTER TABLE users ADD CONSTRAINT users_kind_check CHECK (kind IN ('developer', 'agent'));
  EXCEPTION WHEN duplicate_object THEN NULL; END;
  BEGIN
    ALTER TABLE users ADD CONSTRAINT users_membership_status_check
      CHECK (membership_status IN ('active', 'removed'));
  EXCEPTION WHEN duplicate_object THEN NULL; END;
END $$;

-- 2. projects: an explicit owner (nullable — bootstrap happens on first claim,
--    see POST /projects/:id/owner). ON DELETE SET NULL so removing/cleaning up
--    a user row (test teardowns, member removal) never blocks on this FK.
--    The constraint is (re)created every run so an earlier plain-REFERENCES
--    column (no ON DELETE clause) self-heals instead of staying wrong forever.
ALTER TABLE projects ADD COLUMN IF NOT EXISTS owner_user_id UUID;
ALTER TABLE projects DROP CONSTRAINT IF EXISTS projects_owner_user_id_fkey;
ALTER TABLE projects
  ADD CONSTRAINT projects_owner_user_id_fkey
  FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL;

-- 3. Indexes supporting the new roster/status queries.
CREATE INDEX IF NOT EXISTS idx_users_project_kind   ON users(project_id, kind);
CREATE INDEX IF NOT EXISTS idx_users_project_status ON users(project_id, membership_status);

-- 4. Realtime: the Team dashboard subscribes to `users` the same way the
--    board already subscribes to tasks/decisions/events (migrate_day3.sql).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    CREATE PUBLICATION supabase_realtime;
  END IF;
  BEGIN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.users;
  EXCEPTION WHEN duplicate_object THEN NULL; END;
END $$;
