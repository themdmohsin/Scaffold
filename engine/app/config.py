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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
