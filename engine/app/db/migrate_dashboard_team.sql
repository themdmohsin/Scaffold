-- Phase 6.5 (dashboard team app) — additive only.
-- projects.github_repo: optional "owner/repo" link shown in the create wizard
-- and on the project Settings page. Metadata only — no webhook registration
-- happens here (POST /projects/:id/github-webhook is unchanged).
-- Idempotent like every migration in the manifest.

ALTER TABLE projects ADD COLUMN IF NOT EXISTS github_repo TEXT;
