"""Day 9 verification: DEPLOYMENT HARDENING.

Run from engine/:  python -m tests.test_deploy

Layers, same convention as every harness in this repo:

  1. PURE units (no DB): migration manifest order + statement splitter,
     secret redaction, config validation (dev vs production), rate-limit
     classification + sliding window, invite code uniqueness helper.
  2. ROUTE legs via TestClient: /health stays a pure liveness probe, /ready
     reports DB + migration state (503 when not ready, 200 when ready),
     X-Request-Id echo, rate limiting (429 + Retry-After + X-RateLimit-*),
     and the invite same-second uniqueness fix.
  3. DB legs (skipped loudly without DATABASE_URL): schema_migrations
     idempotency — a second `run_pending` is a no-op; migration_status
     reports no pending and no checksum mismatches.
"""

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-secret")

import tests.auth_helper as auth  # noqa: E402  (sets SUPABASE_JWT_SECRET before app.config)

PASS = []
FAIL = []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))


# ---------------------------------------------------------------------------
# 1. Migration manifest + SQL splitter
# ---------------------------------------------------------------------------
print("\n== pure: migration manifest ==")
from app.db.migrations import (  # noqa: E402
    MIGRATIONS,
    MIGRATIONS_TABLE,
    apply_migration,
    ensure_migrations_table,
    iter_sql_statements,
    migration_checksum,
    migration_path,
)

check("manifest is non-empty", len(MIGRATIONS) >= 6)
versions = [m.version for m in MIGRATIONS]
check("versions are zero-padded and unique", len(set(versions)) == len(versions) and versions == sorted(versions))
check("versions sort lexically in manifest order", [f"{i:04d}" for i in range(1, len(MIGRATIONS) + 1)] == versions)
check("every manifest file exists on disk", all(migration_path(m).exists() for m in MIGRATIONS))
check("every checksum is a 64-char hex digest", all(len(migration_checksum(m)) == 64 for m in MIGRATIONS))
check("migrations table name is schema_migrations", MIGRATIONS_TABLE == "schema_migrations")
tolerant = {m.version: m.tolerant for m in MIGRATIONS}
check("auth migration is tolerant (Supabase-only statements)", tolerant.get("0006") is True)
check("baseline is strict (all-or-nothing)", tolerant.get("0001") is False)

print("\n== pure: SQL statement splitter ==")
sql = """
-- a comment with a semicolon ; inside
CREATE TABLE t (id int); /* block ; comment */
INSERT INTO t VALUES ('a;b');
DO $$ BEGIN RAISE NOTICE 'x; y'; END $$;
SELECT 1;
"""
stmts = iter_sql_statements(sql)
check("splitter keeps 4 statements", len(stmts) == 4, f"got {len(stmts)}: {stmts}")
check("splitter keeps semicolons inside strings", any("'a;b'" in s for s in stmts))
check("splitter keeps dollar-quoted blocks whole", any(s.startswith("DO $$") and s.endswith("$$") for s in stmts))
check("splitter does not split on semicolons inside comments", "-- a comment with a semicolon ; inside" in stmts[0] and "CREATE TABLE t (id int)" in stmts[0])
check("splitter trims empties", all(s.strip() == s and s for s in stmts))

# ---------------------------------------------------------------------------
# 2. Secret redaction
# ---------------------------------------------------------------------------
print("\n== pure: secret redaction ==")
from app.logging_setup import redact  # noqa: E402

pat = "scaffold_abcdef0123456789abcdef"
check("PAT redacted", pat not in redact(f"saw {pat} in log") and "scaffold_***" in redact(f"saw {pat} in log"))
check("Bearer redacted", "supersecret" not in redact("Authorization: Bearer supersecret"))
jwt_like = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdef"
check("JWT-shaped string redacted", jwt_like not in redact(f"credential {jwt_like}"))
check("password=value redacted", "hunter2" not in redact("password=hunter2"))
dsn = "postgresql+psycopg://scaffold:sup3rs3cr3t@db.example.com:5432/postgres"
check("DSN password redacted", "sup3rs3cr3t" not in redact(dsn) and "db.example.com" in redact(dsn))
check("harmless text untouched", redact("project created") == "project created")

# ---------------------------------------------------------------------------
# 3. Config validation — dev warnings vs production errors
# ---------------------------------------------------------------------------
print("\n== pure: config validation ==")
from app.config import Settings, is_production, validate_settings  # noqa: E402

dev = Settings(
    database_url="",
    supabase_jwt_secret="",
    scaffold_cors_origins="",
    scaffold_env="development",
)
dev_report = validate_settings(dev)
check("development: missing DATABASE_URL is a warning, not an error", dev_report.ok)
check("development: warning names DATABASE_URL", any("DATABASE_URL" in w for w in dev_report.warnings))
check("development: warning names SUPABASE_JWT_SECRET", any("SUPABASE_JWT_SECRET" in w for w in dev_report.warnings))
check("development: warning names CORS wildcard", any("CORS" in w for w in dev_report.warnings))
check("is_production false for development", not is_production(dev))

prod = Settings(
    database_url="",
    supabase_jwt_secret="",
    scaffold_cors_origins="",
    scaffold_env="production",
)
prod_report = validate_settings(prod)
check("production: missing config is fatal (errors)", not prod_report.ok)
check("production: error names DATABASE_URL", any("DATABASE_URL" in e for e in prod_report.errors))
check("production: error names SUPABASE_JWT_SECRET", any("SUPABASE_JWT_SECRET" in e for e in prod_report.errors))
check("production: error names CORS", any("CORS" in e for e in prod_report.errors))
check("is_production true for production", is_production(prod))

bootstrap_armed = Settings(scaffold_env="production", scaffold_bootstrap_account_ids="abc", database_url="x", supabase_jwt_secret="y", scaffold_cors_origins="https://a")
check("production: bootstrap escape hatch is an error", any("BOOTSTRAP" in e for e in validate_settings(bootstrap_armed).errors))

bad_limit = Settings(scaffold_rate_limit_auth_per_minute=0)
check("non-positive rate limit is an error", any("SCAFFOLD_RATE_LIMIT_AUTH_PER_MINUTE" in e for e in validate_settings(bad_limit).errors))

bad_format = Settings(scaffold_log_format="xml")
check("unknown log format is an error", any("SCAFFOLD_LOG_FORMAT" in e for e in validate_settings(bad_format).errors))

clean = Settings(
    scaffold_env="production",
    database_url="postgresql://u:p@h/db",
    supabase_jwt_secret="s",
    scaffold_cors_origins="https://scaffold.example.com",
    github_webhook_secret="",  # explicit: the harness env sets one for the routes
)
check("production: fully configured env has no errors", validate_settings(clean).ok)
check("production: github webhook secret still warns (webhook disabled)", any("GITHUB_WEBHOOK_SECRET" in w for w in validate_settings(clean).warnings))

# ---------------------------------------------------------------------------
# 4. Rate limiting — classification + window semantics
# ---------------------------------------------------------------------------
print("\n== pure: rate limiting ==")
from app.services import rate_limit  # noqa: E402

check("auth scope: /auth/me", rate_limit.classify("GET", "/auth/me") == rate_limit.AUTH_SCOPE)
check("auth scope: /auth/tokens", rate_limit.classify("POST", "/auth/tokens") == rate_limit.AUTH_SCOPE)
check("auth scope: POST /projects/join", rate_limit.classify("POST", "/projects/join") == rate_limit.AUTH_SCOPE)
check("auth scope: POST .../invite", rate_limit.classify("POST", "/projects/abc/invite") == rate_limit.AUTH_SCOPE)
check("reason scope: POST .../reason", rate_limit.classify("POST", "/projects/abc/reason") == rate_limit.REASON_SCOPE)
check("reason scope: GET .../reason is unlimited", rate_limit.classify("GET", "/projects/abc/reason") is None)
check("secret scope: /environment/request", rate_limit.classify("POST", "/projects/abc/environment/request") == rate_limit.SECRET_SCOPE)
check("secret scope: /environment/pull", rate_limit.classify("POST", "/projects/abc/environment/pull") == rate_limit.SECRET_SCOPE)
check("secret scope: POST /environment/variables", rate_limit.classify("POST", "/projects/abc/environment/variables") == rate_limit.SECRET_SCOPE)
check("unlimited: GET /projects/:id/tasks", rate_limit.classify("GET", "/projects/abc/tasks") is None)
check("unlimited: GET /health", rate_limit.classify("GET", "/health") is None)

limiter = rate_limit.SlidingWindowLimiter(window_seconds=60.0)
d1 = limiter.check("k", 2, now=1000.0)
d2 = limiter.check("k", 2, now=1000.5)
d3 = limiter.check("k", 2, now=1001.0)
check("first two hits allowed", d1.allowed and d2.allowed)
check("third hit denied", not d3.allowed)
check("denied decision reports retry_after >= 1", d3.retry_after_seconds >= 1)
check("allowed decision reports remaining", d1.remaining == 1 and d2.remaining == 0)
d4 = limiter.check("k", 2, now=1061.0)
check("hit allowed again after the window", d4.allowed)
check("other keys are unaffected", limiter.check("other", 2, now=1061.0).allowed)

key_a = rate_limit.key_for("auth", "1.2.3.4", "Bearer tokenA")
key_b = rate_limit.key_for("auth", "1.2.3.4", "Bearer tokenB")
check("credential fingerprint separates NAT peers", key_a != key_b)
check("key never contains the raw credential", "tokenA" not in key_a)
check("anonymous key works", rate_limit.key_for("auth", "1.2.3.4", None).startswith("auth:1.2.3.4:"))

# ---------------------------------------------------------------------------
# 5. /health + /ready + request id + rate limiting via TestClient
# ---------------------------------------------------------------------------
print("\n== routes: /health, /ready, request id, rate limiting ==")
from starlette.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app, raise_server_exceptions=False)

health = client.get("/health")
check("/health is 200", health.status_code == 200)
check("/health shape is frozen {status: ok}", health.json() == {"status": "ok"})

ready = client.get("/ready")
check("/ready answers 200 or 503 (never 500)", ready.status_code in (200, 503), str(ready.status_code))
body = ready.json()
check("/ready body shape", {"status", "checks", "version"} <= set(body))
check("/ready status matches code", (body["status"] == "ready") == (ready.status_code == 200))
check("/ready never leaks error detail", isinstance(body["checks"].get("database"), str))
check("/ready reports migrations", body["checks"].get("migrations") in ("ok", "unavailable", "checksum_warning") or str(body["checks"].get("migrations", "")).startswith("pending:"))

echo = client.get("/health", headers={"X-Request-Id": "deploy-test-rid-1"})
check("X-Request-Id echoed", echo.headers.get("x-request-id") == "deploy-test-rid-1")
weird = client.get("/health", headers={"X-Request-Id": "bad id!! with spaces"})
check("unsafe X-Request-Id replaced", weird.headers.get("x-request-id") not in (None, "bad id!! with spaces"))
generated = client.get("/health")
check("missing X-Request-Id generated", bool(generated.headers.get("x-request-id")))

# Rate limiting: retune the auth budget at runtime (settings are read per request).
original_auth_limit = settings.scaffold_rate_limit_auth_per_minute
original_enabled = settings.scaffold_rate_limit_enabled
rate_limit.limiter.reset()
try:
    settings.scaffold_rate_limit_auth_per_minute = 3
    statuses = [client.get("/auth/me").status_code for _ in range(4)]
    check("first 3 auth requests pass the limiter", statuses[:3] == [401, 401, 401], str(statuses))
    check("4th auth request is 429", statuses[3] == 429, str(statuses))
    limited = client.get("/auth/me")
    check("429 carries Retry-After", limited.headers.get("retry-after") is not None)
    check("429 carries X-RateLimit-Limit", limited.headers.get("x-ratelimit-limit") == "3")
    check("429 carries X-RateLimit-Remaining: 0", limited.headers.get("x-ratelimit-remaining") == "0")
    check("429 detail names the scope", "auth" in limited.json().get("detail", ""))

    settings.scaffold_rate_limit_enabled = False
    check("disabled limiter lets requests through", client.get("/auth/me").status_code == 401)
    settings.scaffold_rate_limit_enabled = True

    # Other scopes are independent.
    rate_limit.limiter.reset()
    check("unlimited route not throttled by auth budget", client.get("/projects").status_code in (401, 200))
finally:
    settings.scaffold_rate_limit_auth_per_minute = original_auth_limit
    settings.scaffold_rate_limit_enabled = original_enabled
    rate_limit.limiter.reset()

# ---------------------------------------------------------------------------
# 6. Invite code allocation — same-second uniqueness (regression)
# ---------------------------------------------------------------------------
print("\n== routes: invite same-second uniqueness (regression) ==")
db_url = (settings.database_url or "").strip()
if not db_url:
    print("  SKIP  DATABASE_URL not set on this machine — run on a machine with engine/.env")
else:
    from app.routes.invites import make_code  # noqa: E402

    pid = uuid.uuid4()
    a = make_code(pid, now=1_800_000_000.0, ttl_seconds=900)
    b = make_code(pid, now=1_800_000_000.0, ttl_seconds=900)
    check("same second + same ttl -> identical code (documented format)", a == b)
    check("bumped ttl -> different code", make_code(pid, now=1_800_000_000.0, ttl_seconds=901) != a)

    owner = auth.jwt_for(auth.OWNER_SUB)
    inviter = TestClient(app, raise_server_exceptions=False)
    inviter.headers.update(auth.auth_headers(owner))
    r = inviter.post("/projects", json={"name": f"deploy-invite-{uuid.uuid4().hex[:8]}"})
    check("project created for invite leg", r.status_code == 201, r.text)
    test_pid = r.json()["id"]

    first = inviter.post(f"/projects/{test_pid}/invite", json={"ttl_seconds": 900})
    second = inviter.post(f"/projects/{test_pid}/invite", json={"ttl_seconds": 900})
    check("two same-second invites both succeed", first.status_code == 201 and second.status_code == 201, f"{first.status_code}/{second.status_code}")
    check("the two codes differ", first.json()["code"] != second.json()["code"])

    revoked = inviter.post(f"/projects/{test_pid}/invite", json={"max_uses": 1, "ttl_seconds": 900})
    inviter.delete(f"/projects/{test_pid}/invites/{revoked.json()['invite_id']}")
    after = inviter.post(f"/projects/{test_pid}/invite", json={"max_uses": 1, "ttl_seconds": 900})
    check("revoked-then-new in the same second succeeds", after.status_code == 201, after.text)
    check("code after revoke differs from the revoked one", after.json()["code"] != revoked.json()["code"])

# ---------------------------------------------------------------------------
# 7. DB legs — schema_migrations idempotency
# ---------------------------------------------------------------------------
print("\n== db: schema_migrations idempotency ==")
if not db_url:
    print("  SKIP  DATABASE_URL not set — migration legs need a real database")
else:
    from app.db.migrations import run_pending  # noqa: E402
    from app.db.session import _init  # noqa: E402

    engine = _init(apply_migrations=False).kw["bind"]
    ensure_migrations_table(engine)

    first_run = run_pending(engine)
    second_run = run_pending(engine)
    check("a second run applies nothing (idempotent)", second_run == [], str(second_run))

    status = __import__("app.db.migrations", fromlist=["migration_status"]).migration_status(engine)
    check("no pending migrations", status["pending"] == [], str(status["pending"]))
    check("no checksum mismatches", status["checksum_mismatches"] == [], str(status["checksum_mismatches"]))
    check("no unknown applied versions", status["unknown_applied"] == [], str(status["unknown_applied"]))
    check("all manifest versions recorded", {r["version"] for r in status["applied"]} == {m.version for m in MIGRATIONS})

print(f"\n== RESULT: {len(PASS)} passed, {len(FAIL)} failed ==")
if FAIL:
    print("FAILED:")
    for name in FAIL:
        print(f"  - {name}")
sys.exit(1 if FAIL else 0)
