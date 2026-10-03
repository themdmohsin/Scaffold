-- Auth migration (Phase 6 — real authentication, additive per docs/SCHEMA.md).
-- Idempotent: safe to run any number of times. Auto-applied at engine startup
-- after migrate_phase5.sql (app/db/session.py), ONE STATEMENT AT A TIME with
-- per-statement tolerance there.
--
-- IMPORTANT: this file is PLAIN STATEMENTS ONLY — no dollar-quoted DO blocks,
-- no dynamic-SQL format()/RAISE constructs. psycopg3's simple query protocol
-- rejects any percent sign that looks like a client-side placeholder (even in
-- comments), so this file must stay free of them. Statements that need
-- Supabase-specific pieces (auth.uid(), the supabase_realtime publication,
-- the anon/authenticated roles) simply fail-and-skip on a plain Postgres rig.
--
-- Model:
--   accounts               = Supabase auth.users mirror (auth identity: email, provider)
--   project_members        = membership WITH ROLE (owner|admin|member) + status
--   personal_access_tokens = hashed PATs for plugin/CLI/MCP (never stored raw)
--   invites                = real, revocable invitation rows (single/multi-use, expiry, role)
--   users                  = the per-project roster (frozen Day 1) — GAINS account_id,
--                            keeping every existing roster/task-owner FK untouched.
--
-- The `role` (this DB) vs `supabase_role` (membership role) split avoids
-- colliding with Postgres's reserved `role` column naming on `project_members`.
--
-- TABLE CREATION ORDER (FK dependencies; every statement is IF NOT EXISTS, so
-- re-runs are no-ops): accounts → invites → project_members →
-- invite_redemptions → personal_access_tokens → users.account_id.

-- 1. accounts — one row per authenticated identity (Supabase auth.users).
CREATE TABLE IF NOT EXISTS accounts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  supabase_user_id TEXT NOT NULL UNIQUE,
  email TEXT,
  full_name TEXT,
  avatar_url TEXT,
  auth_provider TEXT NOT NULL DEFAULT 'email',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_login_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_accounts_supabase_user ON accounts(supabase_user_id);

-- 2. invites — real invitation rows replacing the stateless Day-5 signed code.
--    The Day-5 endpoint/response shapes (POST /projects/:id/invite →
--    {invite_url, code, expires_at_epoch}; POST /projects/join) stay compatible.
--    Code format is still project_id.expiry.sig (HMAC) so OLD links keep working;
--    the DB row now makes an invite revocable / bounded / inspectable.
CREATE TABLE IF NOT EXISTS invites (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  code TEXT NOT NULL UNIQUE,
  invited_by UUID REFERENCES accounts(id) ON DELETE SET NULL,
  supabase_role TEXT NOT NULL DEFAULT 'member'
    CHECK (supabase_role IN ('owner', 'admin', 'member')),
  max_uses INTEGER,
  use_count INTEGER NOT NULL DEFAULT 0,
  expires_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_invites_project ON invites(project_id);
CREATE INDEX IF NOT EXISTS idx_invites_code ON invites(code);

-- 3. project_members — roles on top of (not replacing) the frozen users roster.
--    (Created AFTER invites because of the invite_id FK.)
CREATE TABLE IF NOT EXISTS project_members (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  supabase_role TEXT NOT NULL DEFAULT 'member'
    CHECK (supabase_role IN ('owner', 'admin', 'member')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'removed')),
  invited_by UUID REFERENCES accounts(id) ON DELETE SET NULL,
  -- Which invite row redeemed this membership ("shows who joined", req. 4).
  invite_id UUID REFERENCES invites(id) ON DELETE SET NULL,
  joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_id, account_id)
);
CREATE INDEX IF NOT EXISTS idx_project_members_project ON project_members(project_id);
CREATE INDEX IF NOT EXISTS idx_project_members_account ON project_members(account_id);
CREATE INDEX IF NOT EXISTS idx_project_members_project_status ON project_members(project_id, status);

-- 4. invite_redemptions — one row per join: the "who joined" audit trail.
CREATE TABLE IF NOT EXISTS invite_redemptions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  invite_id UUID NOT NULL REFERENCES invites(id) ON DELETE CASCADE,
  account_id UUID REFERENCES accounts(id) ON DELETE CASCADE,
  roster_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  redeemed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (invite_id, account_id)
);
CREATE INDEX IF NOT EXISTS idx_invite_redemptions_invite ON invite_redemptions(invite_id);

-- 5. personal_access_tokens — machine credentials for the plugin / CLI / MCP.
--    token_hash = SHA-256 hex of the raw token; the raw value is shown ONCE at
--    creation and can never be recovered. Tokens are revoked, never deleted, so
--    an audit trail survives. `project_id` (nullable) scopes a token to one
--    project for tooling that should never see the rest of the account's work.
CREATE TABLE IF NOT EXISTS personal_access_tokens (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  name TEXT NOT NULL DEFAULT 'token',
  token_hash TEXT NOT NULL UNIQUE,
  token_prefix TEXT NOT NULL DEFAULT '',
  project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
  scopes TEXT NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_pat_account ON personal_access_tokens(account_id);
CREATE INDEX IF NOT EXISTS idx_pat_project ON personal_access_tokens(project_id);

-- 6. users (frozen Day-1 roster) learns about accounts — nullable: legacy
--    roster rows (pre-auth joins) keep working without one.
--    NOT unique: an account legitimately owns more than one roster row per
--    project — its own humans row plus every AGENT row it registers
--    (POST /projects/:id/agents attaches agents to the caller's account).
ALTER TABLE users ADD COLUMN IF NOT EXISTS account_id UUID REFERENCES accounts(id) ON DELETE SET NULL;
DROP INDEX IF EXISTS idx_users_project_account;
CREATE INDEX IF NOT EXISTS idx_users_project_account ON users(project_id, account_id)
  WHERE account_id IS NOT NULL;
-- (Ownership transfer demotes the previous owner to 'member' in Python —
-- services/auth.py set_project_role — no extra column needed.)

-- 7. Row Level Security — the dashboard reads via the anon key (today, an
--    UNAUTHENTICATED anon connection) plus Supabase Realtime, and the same anon
--    key can hit PostgREST's REST surface directly. Without RLS, ANYONE with
--    the (public-by-design) anon key could read EVERY project's rows by
--    guessing a project UUID. Policies below scope every public data table to
--    ACTIVE MEMBERS ONLY. The engine uses the SERVICE key, which bypasses RLS,
--    so engine behavior is unchanged. On a plain-Postgres rig (no Supabase
--    auth schema/roles) each statement fails-and-skips in app/db/session.py —
--    the engine owns authorization in code there anyway.

ALTER TABLE public.accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.project_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.personal_access_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.task_dependencies ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.decision_affects_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.api_contracts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.commits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.blockers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.environment_variables ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.environment_access ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.projects ENABLE ROW LEVEL SECURITY;

-- 7a. Member-read helper: TRUE when the CALLING auth.uid() is an ACTIVE member
--     of the given project. SECURITY DEFINER (owner = the role applying this
--     file — the engine's service/postgres role) so the member lookup cannot
--     recurse through project_members' own policy. The SQL-standard single-
--     expression body (RETURN ...) needs Postgres 14+; anything older just
--     skips (and with it every policy below).
CREATE OR REPLACE FUNCTION scaffold_is_active_member(p_project UUID) RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
RETURN EXISTS (
  SELECT 1 FROM public.project_members pm
  JOIN public.accounts a ON a.id = pm.account_id
  WHERE pm.project_id = p_project
    AND pm.status = 'active'
    AND a.supabase_user_id = auth.uid()::text
);

-- 7b. Per-table policies (idempotent; re-runs fail-and-skip on duplicate).
DROP POLICY IF EXISTS member_read ON public.users;
CREATE POLICY member_read ON public.users FOR SELECT TO anon, authenticated
USING (scaffold_is_active_member(users.project_id));
DROP POLICY IF EXISTS member_read ON public.tasks;
CREATE POLICY member_read ON public.tasks FOR SELECT TO anon, authenticated
USING (scaffold_is_active_member(tasks.project_id));
DROP POLICY IF EXISTS member_read ON public.decisions;
CREATE POLICY member_read ON public.decisions FOR SELECT TO anon, authenticated
USING (scaffold_is_active_member(decisions.project_id));
DROP POLICY IF EXISTS member_read ON public.api_contracts;
CREATE POLICY member_read ON public.api_contracts FOR SELECT TO anon, authenticated
USING (scaffold_is_active_member(api_contracts.project_id));
DROP POLICY IF EXISTS member_read ON public.events;
CREATE POLICY member_read ON public.events FOR SELECT TO anon, authenticated
USING (scaffold_is_active_member(events.project_id));
DROP POLICY IF EXISTS member_read ON public.blockers;
CREATE POLICY member_read ON public.blockers FOR SELECT TO anon, authenticated
USING (scaffold_is_active_member(blockers.project_id));
DROP POLICY IF EXISTS member_read ON public.environment_variables;
CREATE POLICY member_read ON public.environment_variables FOR SELECT TO anon, authenticated
USING (scaffold_is_active_member(environment_variables.project_id));
-- environment_access has no project_id; scope it through its variable
-- (same subquery pattern as task_dependencies below). The inner SELECT runs
-- under environment_variables' own member_read policy, so non-members see no
-- row and the membership check receives NULL -> FALSE (fail-closed).
DROP POLICY IF EXISTS member_read ON public.environment_access;
CREATE POLICY member_read ON public.environment_access FOR SELECT TO anon, authenticated
USING (scaffold_is_active_member(
  (SELECT ev.project_id FROM public.environment_variables ev
   WHERE ev.id = environment_access.environment_variable_id)
));
DROP POLICY IF EXISTS member_read ON public.invites;
CREATE POLICY member_read ON public.invites FOR SELECT TO anon, authenticated
USING (scaffold_is_active_member(invites.project_id));
DROP POLICY IF EXISTS member_read ON public.commits;
CREATE POLICY member_read ON public.commits FOR SELECT TO anon, authenticated
USING (scaffold_is_active_member(commits.project_id));

-- task_dependencies has no project_id; scope it through its task.
DROP POLICY IF EXISTS member_read ON public.task_dependencies;
CREATE POLICY member_read ON public.task_dependencies FOR SELECT TO anon, authenticated
USING (scaffold_is_active_member(
  (SELECT tk.project_id FROM public.tasks tk WHERE tk.id = task_dependencies.task_id)
));

-- decision_affects_tasks rows are scoped through the edge's task.
DROP POLICY IF EXISTS member_read ON public.decision_affects_tasks;
CREATE POLICY member_read ON public.decision_affects_tasks FOR SELECT TO anon, authenticated
USING (scaffold_is_active_member(
  (SELECT tk.project_id FROM public.tasks tk WHERE tk.id = decision_affects_tasks.task_id)
));

-- projects: readable when you are an active member of THAT project.
DROP POLICY IF EXISTS member_read ON public.projects;
CREATE POLICY member_read ON public.projects FOR SELECT TO anon, authenticated
USING (scaffold_is_active_member(projects.id));

-- project_members: you may see your OWN membership rows anywhere, plus the
-- roster of projects you are on (the dashboard's team panel needs both).
DROP POLICY IF EXISTS member_read ON public.project_members;
CREATE POLICY member_read ON public.project_members FOR SELECT TO anon, authenticated
USING (
  account_id IN (SELECT id FROM public.accounts WHERE supabase_user_id = auth.uid()::text)
  OR scaffold_is_active_member(project_members.project_id)
);

-- accounts: a signed-in user may read only their OWN account row.
DROP POLICY IF EXISTS self_read ON public.accounts;
CREATE POLICY self_read ON public.accounts FOR SELECT TO anon, authenticated
USING (supabase_user_id = auth.uid()::text);

-- Writes are ENGINE-ONLY: no INSERT/UPDATE/DELETE policy is granted to
-- anon/authenticated on any table (the service key bypasses RLS).

-- 8. Realtime: the dashboard's live board needs invites visibility for the
--    Team panel (who was invited, what code is live). Wrapped in an EXCEPTION
--    block so a re-run (duplicate_object) or plain Postgres (undefined_object)
--    never counts as a skipped statement.
DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE public.invites;
EXCEPTION WHEN duplicate_object OR undefined_object THEN NULL;
END $$;
