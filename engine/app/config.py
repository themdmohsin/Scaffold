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
    # Optional Day 4 Part B — "owner/repo" the conflict-issue action files against.
    scaffold_github_repo: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
