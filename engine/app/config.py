"""Engine configuration from environment variables.

Names are frozen in docs/API_CONTRACTS.md — never rename; add to engine/.env.example.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = ""
    supabase_url: str = ""
    supabase_service_key: str = ""
    scaffold_team_llm_key: str = ""
    github_webhook_secret: str = ""
    github_token: str = ""
    # Optional: default project for MCP tools when project_id is omitted (demo convention).
    scaffold_default_project_id: str = ""
    # Optional Day 3 overrides — defaults: gemini/gemini-2.5-flash + gemini/gemini-embedding-001
    scaffold_llm_model: str = ""
    scaffold_embed_model: str = ""
    # Optional Phase 4 Part B — "owner/repo" the conflict-issue action files against.
    scaffold_github_repo: str = ""
    # Optional Phase 5 — name of the scaffold_secrets.keyring row used to wrap
    # secret values. Unset: the newest keyring row is used (the migration
    # bootstraps one named 'default' with a server-generated random key).
    # Set it ONLY to pin a specific key across redeploys (see docs/SCHEMA.md).
    scaffold_secret_keyring: str = ""
    # Phase 6 (real authentication). The engine verifies Supabase Auth JWTs with
    # the project's JWT secret (Supabase Dashboard → Settings → API → JWT Secret)
    # and derives the caller's identity from the token — never from a body/query
    # field. Tokens use the HS256 signing key; if Supabase rotates to asymmetric
    # keys, paste the public key(s) here instead.
    supabase_jwt_secret: str = ""
    # Comma-separated list of browser origins allowed to call the engine (CORS).
    # Empty (dev default) keeps the historical wildcard; ALWAYS set in any shared
    # deployment, e.g. "https://scaffold.example.com,http://localhost:5173".
    scaffold_cors_origins: str = ""
    # Bootstrap escape hatch: a comma-separated list of Supabase user ids
    # (auth.users.id, the `sub` claim) that are auto-promoted to OWNER on any
    # project they touch. Empty = disabled. DEV/SEED ONLY — never set in
    # production. Exists so pre-auth projects (no members rows yet) can be
    # claimed without hand-writing DB rows.
    scaffold_bootstrap_account_ids: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
