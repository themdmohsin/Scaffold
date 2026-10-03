-- Scaffold database schema — mirrors docs/SCHEMA.md exactly (frozen Day 1, 2026-09-23).
-- Run this in the Supabase SQL editor (or psql) to create everything, or let the
-- engine's migration runner apply it (it is migration 0001 in the manifest at
-- engine/app/db/migrations.py — `python -m app.scripts.migrate` / startup).
-- pgvector embedding columns are added Day 3 (see docs/SCHEMA.md), not here.
-- Phase 2 task-board columns (description/priority/blocked/created_by/completed_at,
-- widened status CHECK) are added by engine/app/db/migrate_phase2.sql, not here —
-- this file stays the Day 1 baseline for a brand-new database; the migration
-- runner applies every later file in order either way.
--
-- IDEMPOTENT (table/index names carry IF NOT EXISTS) so the runner can adopt an
-- existing database created before schema_migrations existed: re-running this
-- file against a populated DB is a no-op.

CREATE TABLE IF NOT EXISTS projects (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  goal TEXT,
  deadline TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  name TEXT NOT NULL,
  role TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  title TEXT NOT NULL,
  status TEXT DEFAULT 'todo',       -- 'todo' | 'in_progress' | 'done'
  owner_id UUID REFERENCES users(id),
  due_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT tasks_status_check CHECK (status IN ('todo', 'in_progress', 'done'))
);

CREATE TABLE IF NOT EXISTS task_dependencies (
  task_id UUID REFERENCES tasks(id),
  depends_on_task_id UUID REFERENCES tasks(id),
  PRIMARY KEY (task_id, depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS decisions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  text TEXT NOT NULL,
  reasoning TEXT,
  made_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS decision_affects_tasks (
  decision_id UUID REFERENCES decisions(id),
  task_id UUID REFERENCES tasks(id),
  PRIMARY KEY (decision_id, task_id)
);

CREATE TABLE IF NOT EXISTS api_contracts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  route TEXT NOT NULL,              -- e.g. '/api/auth/login'
  method TEXT NOT NULL,             -- 'GET' | 'POST' | etc
  request_schema JSONB,
  response_schema JSONB,
  created_by_task_id UUID REFERENCES tasks(id),
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS commits (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  sha TEXT NOT NULL,
  message TEXT,
  author TEXT,
  files_changed TEXT[],
  summary TEXT,                     -- LLM-generated one-liner, never raw source (repo rule #4)
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS blockers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  task_id UUID REFERENCES tasks(id),
  description TEXT,
  resolved BOOLEAN DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  type TEXT NOT NULL,               -- 'task_created' | 'decision_logged' | 'commit_ingested' | 'conflict_flagged' | ...
  payload JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Indexes (Day 1 additions — see docs/SCHEMA.md)
CREATE INDEX IF NOT EXISTS idx_tasks_project        ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_project_status ON tasks(project_id, status);
CREATE INDEX IF NOT EXISTS idx_users_project        ON users(project_id);
CREATE INDEX IF NOT EXISTS idx_decisions_project    ON decisions(project_id);
CREATE INDEX IF NOT EXISTS idx_contracts_project    ON api_contracts(project_id, method, route);
CREATE INDEX IF NOT EXISTS idx_commits_project      ON commits(project_id, sha);
CREATE INDEX IF NOT EXISTS idx_events_project       ON events(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_blockers_project     ON blockers(project_id) WHERE resolved = false;
