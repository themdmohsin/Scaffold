"""Engine configuration from environment variables.

Names are frozen in docs/API_CONTRACTS.md — never rename; add to engine/.env.example.

`validate_settings()` is the single startup validation point: it turns a
misconfigured environment into actionable messages instead of stack traces at
first request time. In development (SCAFFOLD_ENV unset/"development") problems
are warnings — the engine still boots so a new clone can serve /health. In
production (SCAFFOLD_ENV=production) the same problems are fatal at startup.
"""

from dataclasses import dataclass, field

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

    # ------------------------------------------------------------------
    # Deployment hardening (Day 9) — see docs/DEPLOYMENT.md + OPERATIONS.md
    # ------------------------------------------------------------------
    # "development" (default) or "production". Production makes the validation
    # below fatal at startup and is what deploy guides set.
    scaffold_env: str = "development"
    # Structured logging: json (default, one object per line) or text.
    scaffold_log_format: str = "json"
    scaffold_log_level: str = "INFO"
    # Basic in-process rate limiting for auth / LLM / secret-value endpoints.
    # In-process means per engine replica — enough for one shared engine; a
    # multi-replica deployment needs an edge limiter (documented in OPERATIONS.md).
    scaffold_rate_limit_enabled: bool = True
    scaffold_rate_limit_auth_per_minute: int = 120
    scaffold_rate_limit_reason_per_minute: int = 30
    scaffold_rate_limit_secret_per_minute: int = 240
    # Trust X-Forwarded-For's first hop for the client IP used in rate-limit keys
    # and access logs. Enable ONLY behind a proxy you control (Fly/Railway edge).
    scaffold_trust_proxy: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def lines(self) -> list[str]:
        return [f"ERROR: {e}" for e in self.errors] + [f"WARNING: {w}" for w in self.warnings]


def is_production(s: Settings | None = None) -> bool:
    env = (s or settings).scaffold_env.strip().lower()
    return env in ("production", "prod")


def validate_settings(s: Settings | None = None) -> ValidationReport:
    """Central startup validation. Actionable messages, no secrets, no stack traces."""
    s = s or settings
    report = ValidationReport()
    prod = is_production(s)

    env = s.scaffold_env.strip().lower()
    if env not in ("development", "dev", "production", "prod"):
        report.errors.append(
            f"SCAFFOLD_ENV must be 'development' or 'production' (got {s.scaffold_env!r})."
        )

    # Database — in dev the engine intentionally boots without one (serves /health).
    if not s.database_url:
        msg = (
            "DATABASE_URL is not set. Copy engine/.env.example to engine/.env and paste the "
            "Supabase connection string (Supabase > Project Settings > Database > Connection string)."
        )
        (report.errors if prod else report.warnings).append(msg)

    # Auth — without the JWT secret every authenticated route 503s (fail closed).
    if not s.supabase_jwt_secret:
        msg = (
            "SUPABASE_JWT_SECRET is not set — every authenticated route and MCP tool will return "
            "503 (fail closed). Find it at Supabase > Settings > API > JWT Secret."
        )
        (report.errors if prod else report.warnings).append(msg)

    # CORS — wildcard is dev-only.
    if not s.scaffold_cors_origins.strip():
        msg = (
            "SCAFFOLD_CORS_ORIGINS is empty — browsers from ANY origin may call this engine "
            "(wildcard, credentials disabled). Set the dashboard origin(s), comma-separated, "
            "e.g. https://scaffold.example.com."
        )
        (report.errors if prod else report.warnings).append(msg)

    # Bootstrap escape hatch must never be armed in production.
    if s.scaffold_bootstrap_account_ids.strip():
        msg = (
            "SCAFFOLD_BOOTSTRAP_ACCOUNT_IDS is set — anyone listed is auto-promoted to OWNER on "
            "projects they touch. DEV/SEED ONLY: remove it before sharing this engine."
        )
        (report.errors if prod else report.warnings).append(msg)

    if not s.supabase_url:
        report.warnings.append(
            "SUPABASE_URL is not set — JWT issuer validation falls back to the secret only and "
            "Supabase Realtime/docs links are unavailable. Set it to https://<ref>.supabase.co."
        )

    if not s.scaffold_team_llm_key:
        report.warnings.append(
            "SCAFFOLD_TEAM_LLM_KEY is not set — POST /reason and embedding/backfill calls will "
            "return 503. That key pays for the engine's own reasoning calls only (never a "
            "developer's coding session)."
        )

    if not s.github_webhook_secret:
        report.warnings.append(
            "GITHUB_WEBHOOK_SECRET is not set — POST /projects/:id/github-webhook will reject "
            "every delivery. Set the same secret in GitHub → repo → Settings → Webhooks."
        )

    for name, value in (
        ("SCAFFOLD_RATE_LIMIT_AUTH_PER_MINUTE", s.scaffold_rate_limit_auth_per_minute),
        ("SCAFFOLD_RATE_LIMIT_REASON_PER_MINUTE", s.scaffold_rate_limit_reason_per_minute),
        ("SCAFFOLD_RATE_LIMIT_SECRET_PER_MINUTE", s.scaffold_rate_limit_secret_per_minute),
    ):
        if value <= 0:
            report.errors.append(f"{name} must be a positive integer (got {value}).")

    fmt = s.scaffold_log_format.strip().lower()
    if fmt not in ("json", "text"):
        report.errors.append(f"SCAFFOLD_LOG_FORMAT must be 'json' or 'text' (got {s.scaffold_log_format!r}).")

    return report
