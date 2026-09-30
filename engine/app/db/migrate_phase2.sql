-- Phase 2 migration — Project Control Center (task board + coordination layer).
-- Idempotent: safe to run any number of times. The engine auto-applies this once
-- per process at startup (app/db/session.py), right after the Day 3 migration.
-- Matches docs/SCHEMA.md (Phase 2 additions). Purely additive per the SCHEMA.md
-- freeze rule: new nullable columns, a widened status CHECK (new 'review' value,
-- old values untouched), and two more tables joining the existing realtime
-- publication. No renames, no drops, no type changes on existing columns.

-- 1. New task columns (all nullable / defaulted — existing rows stay valid)
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS priority TEXT DEFAULT 'medium';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS blocked BOOLEAN DEFAULT false;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES users(id);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;

-- 2. Widen the status CHECK to add 'review' (TODO / IN_PROGRESS / REVIEW / DONE).
--    Existing 'todo' | 'in_progress' | 'done' rows remain valid; nothing is renamed.
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_status_check;
ALTER TABLE tasks ADD CONSTRAINT tasks_status_check
  CHECK (status IN ('todo', 'in_progress', 'review', 'done'));

-- 3. Priority CHECK (added the same defensive way — DO block so a re-run that
--    finds the constraint already present is a no-op instead of an error).
DO $$
BEGIN
  ALTER TABLE tasks ADD CONSTRAINT tasks_priority_check
    CHECK (priority IN ('low', 'medium', 'high', 'urgent'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 4. Backfill existing rows with a default priority so the CHECK above holds
--    for rows written before this column existed.
UPDATE tasks SET priority = 'medium' WHERE priority IS NULL;
UPDATE tasks SET blocked = false WHERE blocked IS NULL;

-- 5. Supporting indexes for the task board / control-center queries.
CREATE INDEX IF NOT EXISTS idx_tasks_project_priority ON tasks(project_id, priority);
CREATE INDEX IF NOT EXISTS idx_tasks_project_blocked   ON tasks(project_id) WHERE blocked = true;
CREATE INDEX IF NOT EXISTS idx_task_dependencies_depends_on ON task_dependencies(depends_on_task_id);
CREATE INDEX IF NOT EXISTS idx_blockers_project_task   ON blockers(project_id, task_id);

-- 6. Realtime: the dashboard now also needs live updates for blockers
--    (Blockers panel) and task_dependencies (dependency chips). tasks/decisions/
--    events were already added on Day 3; this only adds the two new tables.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    CREATE PUBLICATION supabase_realtime;
  END IF;
  BEGIN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.blockers;
  EXCEPTION WHEN duplicate_object THEN NULL; END;
  BEGIN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.task_dependencies;
  EXCEPTION WHEN duplicate_object THEN NULL; END;
END $$;
