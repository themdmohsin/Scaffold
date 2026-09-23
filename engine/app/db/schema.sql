-- Scaffold database schema — mirrors docs/SCHEMA.md exactly (frozen Day 1, 2026-09-23).
-- Run this in the Supabase SQL editor (or psql) to create everything.
-- pgvector embedding columns are added Day 3 (see docs/SCHEMA.md), not here.

CREATE TABLE projects (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  goal TEXT,
  deadline TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  name TEXT NOT NULL,
  role TEXT
);

CREATE TABLE tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  title TEXT NOT NULL,
  status TEXT DEFAULT 'todo',       -- 'todo' | 'in_progress' | 'done'
  owner_id UUID REFERENCES users(id),
  due_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT tasks_status_check CHECK (status IN ('todo', 'in_progress', 'done'))
);

CREATE TABLE task_dependencies (
  task_id UUID REFERENCES tasks(id),
  depends_on_task_id UUID REFERENCES tasks(id),
  PRIMARY KEY (task_id, depends_on_task_id)
);

CREATE TABLE decisions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  text TEXT NOT NULL,
  reasoning TEXT,
  made_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE decision_affects_tasks (
  decision_id UUID REFERENCES decisions(id),
  task_id UUID REFERENCES tasks(id),
  PRIMARY KEY (decision_id, task_id)
);

CREATE TABLE api_contracts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  route TEXT NOT NULL,              -- e.g. '/api/auth/login'
  method TEXT NOT NULL,             -- 'GET' | 'POST' | etc
  request_schema JSONB,
  response_schema JSONB,
  created_by_task_id UUID REFERENCES tasks(id),
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE commits (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  sha TEXT NOT NULL,
  message TEXT,
  author TEXT,
  files_changed TEXT[],
  summary TEXT,                     -- LLM-generated one-liner, never raw source (repo rule #4)
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE blockers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  task_id UUID REFERENCES tasks(id),
  description TEXT,
  resolved BOOLEAN DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  type TEXT NOT NULL,               -- 'task_created' | 'decision_logged' | 'commit_ingested' | 'conflict_flagged' | ...
  payload JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Indexes (Day 1 additions — see docs/SCHEMA.md)
CREATE INDEX idx_tasks_project        ON tasks(project_id);
CREATE INDEX idx_tasks_project_status ON tasks(project_id, status);
CREATE INDEX idx_users_project        ON users(project_id);
CREATE INDEX idx_decisions_project    ON decisions(project_id);
CREATE INDEX idx_contracts_project    ON api_contracts(project_id, method, route);
CREATE INDEX idx_commits_project      ON commits(project_id, sha);
CREATE INDEX idx_events_project       ON events(project_id, created_at);
CREATE INDEX idx_blockers_project     ON blockers(project_id) WHERE resolved = false;
