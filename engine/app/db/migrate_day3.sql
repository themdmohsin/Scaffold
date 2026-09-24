-- Day 3 migration — pgvector embeddings + Supabase Realtime publication.
-- Idempotent: safe to run any number of times. The engine auto-applies this once
-- per process at startup (app/db/session.py); you can also run it manually in the
-- Supabase SQL editor. Matches docs/SCHEMA.md (Day 3 additions).

-- 1. pgvector extension (Supabase ships it; postgres role may create it)
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Embedding columns — 1536 dims (gemini-embedding-001 via LiteLLM)
ALTER TABLE decisions     ADD COLUMN IF NOT EXISTS embedding vector(1536);
ALTER TABLE api_contracts ADD COLUMN IF NOT EXISTS embedding vector(1536);

-- 3. HNSW indexes for cosine-distance search
CREATE INDEX IF NOT EXISTS idx_decisions_embedding
  ON decisions USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_contracts_embedding
  ON api_contracts USING hnsw (embedding vector_cosine_ops);

-- 4. Realtime (dashboard subscribes to tasks / decisions / events).
--    Supabase creates the supabase_realtime publication; add our tables if absent.
--    (No percent signs anywhere in this file: it is executed via psycopg's
--    exec_driver_sql, which treats a bare percent sign as a bind placeholder.)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    CREATE PUBLICATION supabase_realtime;
  END IF;
  BEGIN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.tasks;
  EXCEPTION WHEN duplicate_object THEN NULL; END;
  BEGIN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.decisions;
  EXCEPTION WHEN duplicate_object THEN NULL; END;
  BEGIN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.events;
  EXCEPTION WHEN duplicate_object THEN NULL; END;
END $$;
