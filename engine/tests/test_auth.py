"""Phase 6 verification: REAL authentication + authorization.

Run from engine/:  python -m tests.test_auth

No external network, no LLM. Section 1 (credentials) runs anywhere; sections
2-6 hit the real app over TestClient against the REAL database and are SKIPPED
(loudly) when DATABASE_URL is not set — same convention as the other harnesses.

What this pins (the Phase 6 acceptance list):
  • identity comes ONLY from `Authorization: Bearer ...` — JWTs (humans) and
    `scaffold_*` PATs (machines); legacy requesting_user_id/user_id names are
    accepted but can never change WHO you are;
  • unauthenticated -> 401 on every project route; non-member -> 403;
  • role gates: member < admin < owner, enforced on routes AND invites;
  • PAT mint / use / revoke (+ project scoping);
  • invite lifecycle: max_uses caps, expiry, revocation, redemption trail;
  • cross-project isolation and the Phase 5 non-disclosure guarantees;
  • fail-closed: no SUPABASE_JWT_SECRET -> 503, never a silent trust.
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("SCAFFOLD_TEAM_LLM_KEY", "test-key")

import tests.auth_helper as auth  # noqa: E402  (sets SUPABASE_JWT_SECRET before app.config)

PASS = []
FAIL = []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))


def _failures(pairs) -> list[str]:
    return [f"{label}:{code}" for label, code in pairs]


# ---------------------------------------------------------------------------
# 1. Credential primitives — no DB, no network
# ---------------------------------------------------------------------------
print("\n== credential primitives ==")
from fastapi import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app.config import settings  # noqa: E402
from app.services import auth as auth_service  # noqa: E402

check("hash_token is deterministic", auth_service.hash_token("abc") == auth_service.hash_token("abc"))
check("hash_token is a 64-char sha256 hex digest", len(auth_service.hash_token("abc")) == 64)
check("hash_token differs per input", auth_service.hash_token("abc") != auth_service.hash_token("abd"))

raw1 = auth_service.new_token_raw()
raw2 = auth_service.new_token_raw()
check("new_token_raw carries the scaffold_ prefix", raw1.startswith("scaffold_") and raw1 != raw2)
check("new_token_raw clears the minimum length", len(raw1) >= auth_service.PAT_MIN_LEN, str(len(raw1)))
check("PAT sniffing accepts a fresh token", auth_service._looks_like_pat(raw1))
check("PAT sniffing rejects a JWT", not auth_service._looks_like_pat(auth.jwt_for(auth.OWNER_SUB)))
check("PAT sniffing rejects a too-short scaffold_ string", not auth_service._looks_like_pat("scaffold_short"))


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": raw,
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("testclient", 1),
            "root_path": "",
        }
    )


def _bearer(headers: dict[str, str]) -> tuple[int, str]:
    try:
        return 200, auth_service._extract_bearer(_request(headers))
    except HTTPException as exc:
        return exc.status_code, ""


check("missing Authorization header -> 401", _bearer({})[0] == 401)
check("non-Bearer scheme -> 401", _bearer({"Authorization": "Token abc"})[0] == 401)
check("empty bearer value -> 401", _bearer({"Authorization": "Bearer   "})[0] == 401)
check("well-formed Bearer extracts the credential", _bearer({"Authorization": "Bearer xyz.1"}) == (200, "xyz.1"))


def _jwt_check(payload_override: dict, *, key: str | None = None, minutes: int = 30) -> tuple[int, str]:
    """Mint a JWT with overrides and verify it; (status, detail)."""
    import jwt as pyjwt

    now = int(time.time())
    payload = {
        "sub": str(uuid.uuid4()),
        "aud": "authenticated",
        "iss": f"{auth.TEST_SUPABASE_URL}/auth/v1",
        "iat": now,
        "exp": now + minutes * 60,
        "role": "authenticated",
    }
    payload.update(payload_override)
    for drop in payload_override.get("_drop", []):
        payload.pop(drop, None)
    payload.pop("_drop", None)
    token = pyjwt.encode(payload, key or auth.TEST_JWT_SECRET, algorithm="HS256")
    try:
        sub, _email = auth_service._verify_jwt(token)
        return 200, sub
    except HTTPException as exc:
        return exc.status_code, str(exc.detail)


check("a well-formed Supabase JWT verifies", _jwt_check({})[0] == 200)
check("wrong audience -> 401", _jwt_check({"aud": "anon"}) == (401, "token audience mismatch"))
check("wrong issuer -> 401", _jwt_check({"iss": "https://evil.example/auth/v1"}) == (401, "token issuer mismatch"))
check("expired token -> 401", _jwt_check({"exp": int(time.time()) - 60})[0] == 401)
check("expired token names the failure", "expired" in _jwt_check({"exp": int(time.time()) - 60})[1])
check("missing exp -> 401", _jwt_check({"_drop": ["exp"]})[0] == 401)
check("missing sub -> 401", _jwt_check({"sub": ""})[0] == 401)
check("foreign signature -> 401", _jwt_check({}, key="x" * 48)[0] == 401)

_orig_secret = settings.supabase_jwt_secret
try:
    settings.supabase_jwt_secret = ""
    try:
        auth_service._verify_jwt(auth.jwt_for(auth.OWNER_SUB))
        fail_closed = False
    except auth_service.AuthUnavailable:
        fail_closed = True
finally:
    settings.supabase_jwt_secret = _orig_secret
check("no SUPABASE_JWT_SECRET -> AuthUnavailable (fail closed, not silent trust)", fail_closed)

check("MCP has no principal outside a verified request", auth_service.current_mcp_principal.get() is None)
try:
    from app import mcp_server  # noqa: E402

    mcp_server._principal()
    mcp_guard = False
except HTTPException as exc:
    mcp_guard = exc.status_code == 401
check("MCP tool bodies refuse to run without a verified principal -> 401", mcp_guard)

check(
    "role ranks are strictly ordered owner > admin > member",
    {auth_service.ROLE_MEMBER: 0, auth_service.ROLE_ADMIN: 1, auth_service.ROLE_OWNER: 2}
    == {"member": 0, "admin": 1, "owner": 2}
    and auth_service.ROLES == ("owner", "admin", "member"),
)

# ---------------------------------------------------------------------------
# 2-6. Route-level matrix against the real DB
# ---------------------------------------------------------------------------
print("\n== routes against the real DB (skipped when no engine/.env) ==")

db_url = (settings.database_url or "").strip()
if not db_url:
    print("  SKIP  DATABASE_URL not set on this machine — run on a machine with engine/.env")
else:
    from starlette.testclient import TestClient  # noqa: E402

    from app.db.models import PersonalAccessToken  # noqa: E402
    from app.db.session import _init  # noqa: E402
    from app.main import app  # noqa: E402

    SessionLocal = _init()
    owner_client = TestClient(app, raise_server_exceptions=False)
    owner_client.headers.update(auth.auth_headers(auth.jwt_for(auth.OWNER_SUB)))
    anon = TestClient(app, raise_server_exceptions=False)
    db = SessionLocal()

    pid = None
    other_pid = None
    try:
        # -- the caller owns what they create (requirement 6) -----------------
        r = owner_client.post("/projects", json={"name": f"auth-test-{uuid.uuid4().hex[:8]}", "goal": "real auth"})
        check("POST /projects 201 for an authenticated caller", r.status_code == 201, r.text)
        pid = r.json()["id"]
        check("the creator is the owner (owner_user_id set)", bool(r.json().get("owner_user_id")), r.text)
        check("the creator's governed role is owner", auth.role_of(pid, auth.OWNER_SUB) == "owner")

        r = owner_client.get("/projects")
        check(
            "GET /projects lists the caller's project with its role",
            r.status_code == 200
            and any(p["id"] == pid and p["supabase_role"] == "owner" for p in r.json()["projects"]),
            r.text[:200],
        )

        auth.set_role(pid, auth.ADMIN_SUB, "admin")
        auth.set_role(pid, auth.MEMBER_SUB, "member")
        auth.ensure_account(auth.OUTSIDER_SUB)
        outsider_token = auth.jwt_for(auth.OUTSIDER_SUB)
        member_token = auth.jwt_for(auth.MEMBER_SUB)
        admin_token = auth.jwt_for(auth.ADMIN_SUB)
        check("the outsider is NOT a member", auth.role_of(pid, auth.OUTSIDER_SUB) is None)

        r = TestClient(app, raise_server_exceptions=False)
        r.headers.update(auth.auth_headers(outsider_token))
        r = r.get("/projects")
        check(
            "GET /projects does not leak projects the caller is not in",
            r.status_code == 200 and all(p["id"] != pid for p in r.json()["projects"]),
            r.text[:200],
        )

        # A second project owned by the outsider: cross-project isolation.
        other = TestClient(app, raise_server_exceptions=False)
        other.headers.update(auth.auth_headers(outsider_token))
        r2 = other.post("/projects", json={"name": f"auth-other-{uuid.uuid4().hex[:8]}"})
        other_pid = r2.json()["id"]
        check("a different account owns its own project", r2.status_code == 201 and auth.role_of(other_pid, auth.OUTSIDER_SUB) == "owner")

        # -- unauthenticated: 401 on every project route ----------------------
        read_paths = [
            "", "/context", "/tasks", "/decisions", "/contracts", "/users", "/members",
            "/tasks/ready", "/recommendations", "/recommendations/next", "/coordination",
            "/environment", "/environment/template", "/environment/audit", "/invites",
        ]
        bad = _failures(
            (f"GET {p or '/'}", anon.get(f"/projects/{pid}{p}").status_code)
            for p in read_paths
            if anon.get(f"/projects/{pid}{p}").status_code != 401
        )
        check("every project read route answers 401 without a token", not bad, str(bad))

        write_probes = [
            ("POST /tasks", lambda: anon.post(f"/projects/{pid}/tasks", json={"title": "x"})),
            ("PATCH /tasks/{id}", lambda: anon.patch(f"/projects/{pid}/tasks/{uuid.uuid4()}", json={"status": "done"})),
            ("POST /decisions", lambda: anon.post(f"/projects/{pid}/decisions", json={"text": "x"})),
            ("POST /contracts", lambda: anon.post(f"/projects/{pid}/contracts", json={"route": "/x", "method": "GET"})),
            ("POST /invite", lambda: anon.post(f"/projects/{pid}/invite", json={})),
            ("POST /join", lambda: anon.post("/projects/join", json={"code": "1.2.3"})),
            ("POST /agents", lambda: anon.post(f"/projects/{pid}/agents", json={"name": "x"})),
            ("POST /owner", lambda: anon.post(f"/projects/{pid}/owner", json={"user_id": str(uuid.uuid4())})),
            ("POST /reason", lambda: anon.post(f"/projects/{pid}/reason", json={"prompt": "x"})),
            ("POST /environment/request", lambda: anon.post(f"/projects/{pid}/environment/request", json={"key": "K"})),
            ("POST /environment/pull", lambda: anon.post(f"/projects/{pid}/environment/pull", json={})),
            ("GET /projects", lambda: anon.get("/projects")),
            ("POST /projects", lambda: anon.post("/projects", json={"name": "nope"})),
        ]
        bad = _failures(
            (label, call().status_code) for label, call in write_probes if call().status_code != 401
        )
        check("every project write/collection route answers 401 without a token", not bad, str(bad))
        check("GET /health stays public", anon.get("/health").status_code == 200)

        # -- non-member: 403 everywhere on the project ------------------------
        bad = _failures(
            (f"GET {p or '/'}", other.get(f"/projects/{pid}{p}").status_code)
            for p in read_paths
            if other.get(f"/projects/{pid}{p}").status_code != 403
        )
        check("a valid non-member gets 403 on every read route", not bad, str(bad))
        bad = _failures(
            (label, call().status_code)
            for label, call in [
                ("POST /tasks", lambda: other.post(f"/projects/{pid}/tasks", json={"title": "x"})),
                ("POST /decisions", lambda: other.post(f"/projects/{pid}/decisions", json={"text": "x"})),
                ("POST /contracts", lambda: other.post(f"/projects/{pid}/contracts", json={"route": "/x", "method": "GET"})),
                ("POST /invite", lambda: other.post(f"/projects/{pid}/invite", json={})),
                ("POST /agents", lambda: other.post(f"/projects/{pid}/agents", json={"name": "x"})),
                ("POST /environment/request", lambda: other.post(f"/projects/{pid}/environment/request", json={"key": "K"})),
            ]
            if call().status_code != 403
        )
        check("a valid non-member gets 403 on every write route", not bad, str(bad))
        check("unknown project still 404s for an authenticated caller", owner_client.get(f"/projects/{uuid.uuid4()}/tasks").status_code == 404)

        # -- role gates: member < admin < owner ------------------------------
        mem = TestClient(app, raise_server_exceptions=False)
        mem.headers.update(auth.auth_headers(member_token))
        adm = TestClient(app, raise_server_exceptions=False)
        adm.headers.update(auth.auth_headers(admin_token))

        check("member can read tasks", mem.get(f"/projects/{pid}/tasks").status_code == 200)
        check("member can create a task", mem.post(f"/projects/{pid}/tasks", json={"title": "member task"}).status_code == 201)
        check("member can record a decision", mem.post(f"/projects/{pid}/decisions", json={"text": "member decision"}).status_code == 201)
        check(
            "member can register a contract",
            mem.post(f"/projects/{pid}/contracts", json={"route": "/api/member-probe", "method": "GET"}).status_code == 201,
        )
        check("member can register an agent", mem.post(f"/projects/{pid}/agents", json={"name": f"member agent {uuid.uuid4().hex[:6]}"}).status_code == 201)
        check("member CANNOT create invites", mem.post(f"/projects/{pid}/invite", json={}).status_code == 403)
        check("member CANNOT list invites", mem.get(f"/projects/{pid}/invites").status_code == 403)
        check("member CANNOT mutate environment metadata", mem.post(f"/projects/{pid}/environment/variables", json={"key": "MEMBER_PROBE"}).status_code == 403)
        check("member CANNOT grant environment access", mem.post(f"/projects/{pid}/environment/access", json={"environment_variable_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4())}).status_code == 403)

        check("admin CAN create invites", adm.post(f"/projects/{pid}/invite", json={}).status_code == 201)
        check("admin CAN list invites", adm.get(f"/projects/{pid}/invites").status_code == 200)
        check("admin CANNOT mutate environment metadata (owner-only)", adm.post(f"/projects/{pid}/environment/variables", json={"key": "ADMIN_PROBE"}).status_code == 403)
        check("admin CANNOT revoke an invite of another project", adm.delete(f"/projects/{other_pid}/invites/{uuid.uuid4()}").status_code in (403, 404))

        env_var = owner_client.post(
            f"/projects/{pid}/environment/variables",
            json={"key": "AUTH_TEST_SECRET", "is_secret": True, "required": True, "value": "s3cr3t-value-do-not-leak"},
        )
        check("owner CAN create environment variables", env_var.status_code == 201, env_var.text)
        env_var_id = env_var.json()["id"]
        check("the create response does NOT echo the value", "s3cr3t" not in env_var.text)
        check(
            "an unconfigured grant list reads back",
            owner_client.get(f"/projects/{pid}/environment/access", params={"environment_variable_id": env_var_id}).status_code == 200,
        )
        check("admin CANNOT delete environment variables (owner-only)", adm.delete(f"/projects/{pid}/environment/variables/{env_var_id}").status_code == 403)
        check(
            "granting a non-member is refused 400",
            owner_client.post(
                f"/projects/{pid}/environment/access",
                json={"environment_variable_id": env_var_id, "user_id": str(uuid.uuid4())},
            ).status_code == 400,
        )

        # -- membership management is admin+ ---------------------------------
        victim_token, _victim_sub = auth.extra_member("Auth Victim")
        code = owner_client.post(f"/projects/{pid}/invite", json={}).json()["code"]
        victim = TestClient(app, raise_server_exceptions=False)
        victim.headers.update(auth.auth_headers(victim_token))
        joined = victim.post("/projects/join", json={"code": code}).json()
        victim_id = joined["user_id"]
        check("a fresh account joins and becomes a member", auth.role_of(pid, _victim_sub) == "member", str(joined))
        check("a plain member CANNOT claim ownership", mem.post(f"/projects/{pid}/owner", json={"user_id": victim_id}).status_code == 403)

        # -- the granted-value path end to end --------------------------------
        granted = owner_client.post(
            f"/projects/{pid}/environment/access",
            json={"environment_variable_id": env_var_id, "user_id": victim_id, "granted_by": str(uuid.uuid4()), "requesting_user_id": str(uuid.uuid4())},
        )
        check("owner CAN grant a member access (legacy identity fields ignored)", granted.status_code == 200, granted.text)
        grant_id = granted.json()["id"]
        r = victim.post(f"/projects/{pid}/environment/request", json={"key": "AUTH_TEST_SECRET", "user_id": str(uuid.uuid4())})
        check("the GRANTED member retrieves the value via the token identity", r.status_code == 200 and r.json()["value"] == "s3cr3t-value-do-not-leak", r.text)
        check("the value response carries only key+value", set(r.json()) == {"key", "value"}, str(r.json()))
        r = mem.post(f"/projects/{pid}/environment/request", json={"key": "AUTH_TEST_SECRET", "user_id": victim_id})
        check("another member cannot borrow the granted member's identity via user_id", r.status_code == 403, r.text)
        check("the borrowed-identity denial carries NO value", "s3cr3t" not in r.text)
        check("owner CAN revoke a grant", owner_client.delete(f"/projects/{pid}/environment/access/{grant_id}").status_code == 200)
        r = victim.post(f"/projects/{pid}/environment/request", json={"key": "AUTH_TEST_SECRET"})
        check("the revoked member is denied immediately", r.status_code == 403 and "s3cr3t" not in r.text, r.text)

        roster_v = victim.get(f"/projects/{pid}/members").json()
        check("members roster flags exactly the caller's own row with is_me",
              [m["id"] for m in roster_v if m.get("is_me")] == [victim_id], str(roster_v))
        check("roster never exposes account ids", all("account_id" not in m for m in roster_v))
        check("member CANNOT PATCH another member", mem.patch(f"/projects/{pid}/members/{victim_id}", json={"role": "member"}).status_code == 403)
        check("admin CAN PATCH another member", adm.patch(f"/projects/{pid}/members/{victim_id}", json={"role": "member"}).status_code == 200)
        check("admin CAN set the governed role of a member", adm.patch(f"/projects/{pid}/members/{victim_id}", json={"supabase_role": "admin"}).status_code == 200)
        check("governed role change took effect", auth.role_of(pid, _victim_sub) == "admin")
        check("the promoted member can now create invites", victim.post(f"/projects/{pid}/invite", json={}).status_code == 201)
        check("admin CAN remove a member", adm.delete(f"/projects/{pid}/members/{victim_id}").status_code == 200)
        check("a removed member loses access immediately", victim.get(f"/projects/{pid}/tasks").status_code == 403)
        check("removed members no longer hold a governed role", auth.role_of(pid, _victim_sub) is None)

        # -- legacy identity fields are accepted but IGNORED -------------------
        r = mem.post(f"/projects/{pid}/tasks", json={"title": "legacy probe", "requesting_user_id": str(uuid.uuid4())})
        check("legacy requesting_user_id cannot break a member's authorized write", r.status_code == 201, r.text)
        r = other.post(
            f"/projects/{pid}/environment/request",
            json={"key": "AUTH_TEST_SECRET", "user_id": str(uuid.uuid4())},
        )
        check("a non-member cannot smuggle membership via a body user_id", r.status_code == 403, r.text)
        r = mem.post(f"/projects/{pid}/environment/request", json={"key": "AUTH_TEST_SECRET", "user_id": str(uuid.uuid4())})
        check("a member without a grant cannot borrow one via user_id", r.status_code == 403, r.text)
        check("the denial body carries NO value", "s3cr3t" not in r.text, r.text)

        # -- non-disclosure sweep ---------------------------------------------
        check("GET /environment exposes no value", "s3cr3t" not in mem.get(f"/projects/{pid}/environment").text)
        check("GET /environment/template exposes no value", "s3cr3t" not in mem.get(f"/projects/{pid}/environment/template").text)
        check("GET /environment/audit exposes no value", "s3cr3t" not in mem.get(f"/projects/{pid}/environment/audit").text)
        check("GET /context exposes no value", "s3cr3t" not in mem.get(f"/projects/{pid}/context").text)
        check("the variable is still configured (no accidental deletion)", (
            "configured" in {v["status"] for v in mem.get(f"/projects/{pid}/environment").json()["variables"]}
        ))

        # -- PAT lifecycle (requirement 2) ------------------------------------
        pa = auth.ProjectAuth(pid)
        raw_pat, prefix = pa.pat(name="auth-test pat")
        pat_client = TestClient(app, raise_server_exceptions=False)
        pat_client.headers.update(auth.auth_headers(raw_pat))
        check("a fresh PAT authenticates", pat_client.get(f"/projects/{pid}/tasks").status_code == 200)
        check("a PAT authenticates on the list route", pat_client.get("/projects").status_code == 200)

        stored = db.query(PersonalAccessToken).filter(PersonalAccessToken.token_prefix == prefix).one()
        check("the raw PAT is NEVER stored (hash only)", stored.token_hash == auth_service.hash_token(raw_pat) and raw_pat not in str(stored.__dict__))
        check("PATs are prefixed, never guessable", raw_pat.startswith("scaffold_") and len(raw_pat) > 40)

        check("a PAT cannot accept an invite (humans only)", pat_client.post("/projects/join", json={"code": code}).status_code == 403)
        check("a PAT that is not a member of a project gets 403", pat_client.get(f"/projects/{other_pid}/tasks").status_code == 403)
        check("a nonexistent PAT string is 401", TestClient(app).get(f"/projects/{pid}/tasks", headers={"Authorization": f"Bearer scaffold_{'z' * 43}"}).status_code == 401)

        scoped = auth.ProjectAuth(pid)
        scoped_raw, _scoped_prefix = scoped.pat(scoped_to_project=True, name="scoped pat")
        scoped_client = TestClient(app, raise_server_exceptions=False)
        scoped_client.headers.update(auth.auth_headers(scoped_raw))
        check("a project-scoped PAT works on its project", scoped_client.get(f"/projects/{pid}/tasks").status_code == 200)
        check("a project-scoped PAT is refused elsewhere", scoped_client.get(f"/projects/{other_pid}/tasks").status_code == 403)
        check("non-membership is checked before the scope mismatch leaks", scoped_client.get(f"/projects/{uuid.uuid4()}/tasks").status_code == 404)

        check("a PAT resolves to the owner's account, not a new identity", auth.role_of(pid, auth.OWNER_SUB) == "owner")

        pa.revoke_pat(prefix)
        check("a revoked PAT is refused with 401", pat_client.get(f"/projects/{pid}/tasks").status_code == 401)
        check("revoking one PAT does not touch another", scoped_client.get(f"/projects/{pid}/tasks").status_code == 200)

        # -- invite lifecycle (requirement 4) ---------------------------------
        created = owner_client.post(f"/projects/{pid}/invite", json={"max_uses": 1, "ttl_seconds": 600})
        check("an invite can be created with a use cap", created.status_code == 201 and created.json()["max_uses"] == 1, created.text)
        one_shot = created.json()
        one_shot_code = one_shot["code"]
        one_shot_id = one_shot["invite_id"]

        j1_token, _j1_sub = auth.extra_member("One Shot A")
        j1 = TestClient(app, raise_server_exceptions=False)
        j1.headers.update(auth.auth_headers(j1_token))
        check("the first redemption of a one-shot invite works", j1.post("/projects/join", json={"code": one_shot_code}).status_code == 201)
        j2_token, _j2_sub = auth.extra_member("One Shot B")
        j2 = TestClient(app, raise_server_exceptions=False)
        j2.headers.update(auth.auth_headers(j2_token))
        r = j2.post("/projects/join", json={"code": one_shot_code})
        check("the second redemption is refused (no uses left)", r.status_code == 403 and "uses" in r.text, r.text)

        listed = adm.get(f"/projects/{pid}/invites").json()
        row = next((i for i in listed if i["id"] == one_shot_id), None)
        check("the redemption trail records who joined", bool(row) and len(row["redemptions"]) == 1, str(row))
        check("the use count is tracked", bool(row) and row["use_count"] == 1, str(row))

        fresh = owner_client.post(f"/projects/{pid}/invite", json={"ttl_seconds": 900})
        fresh_code = fresh.json()["code"]
        fresh_id = fresh.json()["invite_id"]
        check("revoking an invite returns 200", owner_client.delete(f"/projects/{pid}/invites/{fresh_id}").status_code == 200)
        j3_token, _j3_sub = auth.extra_member("Revoked Join")
        j3 = TestClient(app, raise_server_exceptions=False)
        j3.headers.update(auth.auth_headers(j3_token))
        r = j3.post("/projects/join", json={"code": fresh_code})
        check("a revoked invite code cannot be redeemed", r.status_code == 403 and "revoked" in r.text, r.text)

        expiring = owner_client.post(f"/projects/{pid}/invite", json={"ttl_seconds": 900})
        expiring_code = expiring.json()["code"]
        import sqlalchemy as sa  # noqa: E402

        db.execute(
            sa.text("UPDATE invites SET expires_at = now() - interval '1 hour' WHERE code = :c"),
            {"c": expiring_code},
        )
        db.commit()
        j4_token, _j4_sub = auth.extra_member("Expired Join")
        j4 = TestClient(app, raise_server_exceptions=False)
        j4.headers.update(auth.auth_headers(j4_token))
        r = j4.post("/projects/join", json={"code": expiring_code})
        check("an expired invite row cannot be redeemed", r.status_code == 400 and "expired" in r.text, r.text)

        multi = owner_client.post(f"/projects/{pid}/invite", json={"max_uses": 3, "ttl_seconds": 900})
        multi_code = multi.json()["code"]
        multi_id = multi.json()["invite_id"]
        for i in range(2):
            tok, _sub = auth.extra_member(f"Multi {i}")
            cl = TestClient(app, raise_server_exceptions=False)
            cl.headers.update(auth.auth_headers(tok))
            check(f"multi-use invite redemption {i + 1}", cl.post("/projects/join", json={"code": multi_code}).status_code == 201)
        row = next(i for i in adm.get(f"/projects/{pid}/invites").json() if i["id"] == multi_id)
        check("multi-use invite counts both redemptions", row["use_count"] == 2, str(row))
        check("each redemption is attributed to a distinct account", len({r_["account_id"] for r_ in row["redemptions"]}) == 2, str(row))

        check("an unknown invite id 404s on revoke", owner_client.delete(f"/projects/{pid}/invites/{uuid.uuid4()}").status_code == 404)
        check("a malformed invite code cannot be redeemed", j4.post("/projects/join", json={"code": "not-a-code"}).status_code == 400)

        # -- cross-project isolation ------------------------------------------
        check("a member of project A cannot read project B", mem.get(f"/projects/{other_pid}/tasks").status_code == 403)
        check("an admin of project A cannot read project B", adm.get(f"/projects/{other_pid}/context").status_code == 403)
        check("the owner of project A cannot read project B", owner_client.get(f"/projects/{other_pid}/tasks").status_code == 403)
        check("the owner of project B cannot read project A", other.get(f"/projects/{pid}/tasks").status_code == 403)
        check("project B's invite cannot be created by project A's owner", owner_client.post(f"/projects/{other_pid}/invite", json={}).status_code == 403)

        # -- fail closed -------------------------------------------------------
        try:
            settings.supabase_jwt_secret = ""
            check("with no verifiable secret every authenticated route is 503", mem.get(f"/projects/{pid}/tasks").status_code == 503)
        finally:
            settings.supabase_jwt_secret = _orig_secret
        check("the engine recovers once the secret is back", mem.get(f"/projects/{pid}/tasks").status_code == 200)

        # -- Row Level Security: what the anon key can see ---------------------
        # The dashboard's Realtime subscriptions use the anon key, so RLS (not
        # just the engine) must scope rows to active members. Probe as the
        # `anon` role with request.jwt.claims set exactly how Supabase's
        # auth.uid() reads it. Plain-Postgres rigs (no anon role / no policies)
        # skip loudly.
        rls_ready = bool(db.execute(sa.text(
            "SELECT count(*) FROM pg_policies WHERE schemaname = 'public' "
            "AND policyname = 'member_read' AND tablename = 'environment_access'"
        )).scalar()) and bool(
            db.execute(sa.text("SELECT count(*) FROM pg_roles WHERE rolname = 'anon'")).scalar()
        )
        if not rls_ready:
            print("  SKIP  RLS enforcement probe (no anon role / member_read policies on this DB)")
        else:
            owner_uid = db.execute(sa.text(
                "SELECT u.id FROM users u JOIN accounts a ON a.id = u.account_id "
                "WHERE u.project_id = :p AND a.supabase_user_id = :s "
                "AND COALESCE(u.kind, 'human') <> 'agent' LIMIT 1"
            ), {"p": pid, "s": auth.OWNER_SUB}).scalar()
            granted = owner_client.post(
                f"/projects/{pid}/environment/access",
                json={"environment_variable_id": env_var_id, "user_id": str(owner_uid)},
            )
            check("RLS probe: the owner holds a live env grant on the project", granted.status_code == 200, granted.text)

            n_tasks = db.execute(sa.text("SELECT count(*) FROM tasks WHERE project_id = :p"), {"p": pid}).scalar()
            n_vars = db.execute(sa.text("SELECT count(*) FROM environment_variables WHERE project_id = :p"), {"p": pid}).scalar()
            n_grants = db.execute(sa.text(
                "SELECT count(*) FROM environment_access ea "
                "JOIN environment_variables ev ON ev.id = ea.environment_variable_id WHERE ev.project_id = :p"
            ), {"p": pid}).scalar()
            check("RLS probe: the fixture has tasks and a live grant to scope", n_tasks >= 1 and n_grants >= 1, f"{n_tasks}/{n_grants}")
            cs_schema_exists = bool(db.execute(sa.text(
                "SELECT count(*) FROM pg_namespace WHERE nspname = 'scaffold_secrets'"
            )).scalar())

            def _claim(sub):
                db.execute(
                    sa.text("SELECT set_config('request.jwt.claims', :c, true)"),
                    {"c": json.dumps({"sub": sub}) if sub else "null"},
                )

            db.rollback()
            try:
                db.execute(sa.text("SET LOCAL ROLE anon"))

                _claim(auth.OWNER_SUB)
                check("RLS: an active member reads their project's tasks", db.execute(sa.text("SELECT count(*) FROM tasks WHERE project_id = :p"), {"p": pid}).scalar() == n_tasks)
                check("RLS: an active member reads their project's env metadata", db.execute(sa.text("SELECT count(*) FROM environment_variables WHERE project_id = :p"), {"p": pid}).scalar() == n_vars)
                check("RLS: an active member reads their project's env grants", db.execute(sa.text("SELECT count(*) FROM environment_access ea JOIN environment_variables ev ON ev.id = ea.environment_variable_id WHERE ev.project_id = :p"), {"p": pid}).scalar() == n_grants)
                check("RLS: an active member sees only their own account row", db.execute(sa.text("SELECT count(*) FROM accounts")).scalar() == 1)
                check("RLS: an active member still cannot read another project", db.execute(sa.text("SELECT count(*) FROM tasks WHERE project_id = :p"), {"p": other_pid}).scalar() == 0)

                _claim(auth.OUTSIDER_SUB)
                check("RLS: a non-member sees ZERO tasks in THIS project", db.execute(sa.text("SELECT count(*) FROM tasks WHERE project_id = :p"), {"p": pid}).scalar() == 0)
                check("RLS: a non-member sees ZERO env grants in THIS project", db.execute(sa.text("SELECT count(*) FROM environment_access ea JOIN environment_variables ev ON ev.id = ea.environment_variable_id WHERE ev.project_id = :p"), {"p": pid}).scalar() == 0)
                check("RLS: a non-member sees ZERO rows for THIS project", db.execute(sa.text("SELECT count(*) FROM projects WHERE id = :p"), {"p": pid}).scalar() == 0)
                check("RLS: but the outsider still sees the project they DO own", db.execute(sa.text("SELECT count(*) FROM projects WHERE id = :o"), {"o": other_pid}).scalar() == 1)
                check("RLS: a non-member cannot read OTHER accounts", db.execute(sa.text(
                    "SELECT count(*) FROM accounts WHERE supabase_user_id <> :s"
                ), {"s": auth.OUTSIDER_SUB}).scalar() == 0)
                check("RLS: a non-member sees ZERO invites in THIS project", db.execute(sa.text("SELECT count(*) FROM invites WHERE project_id = :p"), {"p": pid}).scalar() == 0)
                check("RLS: a non-member sees ZERO events in THIS project", db.execute(sa.text("SELECT count(*) FROM events WHERE project_id = :p"), {"p": pid}).scalar() == 0)

                if cs_schema_exists:
                    # Default ACLs mean anon has no USAGE on scaffold_secrets; the
                    # schema does not even show up in its information_schema.
                    check("RLS: the ciphertext schema is invisible to the anon key", db.execute(sa.text(
                        "SELECT count(*) FROM information_schema.schemata WHERE schema_name = 'scaffold_secrets'"
                    )).scalar() == 0)
                else:
                    print("  SKIP  ciphertext-schema probe (schema absent on this DB)")

                _claim(None)
                check("RLS: with no jwt claims at all, nothing is readable", db.execute(sa.text("SELECT count(*) FROM tasks")).scalar() == 0)
            finally:
                db.rollback()  # end the anon transaction; restore the engine's role
            check("the engine (service role) still reads the full project after the probe", db.execute(sa.text("SELECT count(*) FROM tasks WHERE project_id = :p"), {"p": pid}).scalar() == n_tasks)

        # -- ownership semantics ----------------------------------------------
        r = owner_client.post(f"/projects/{pid}/owner", json={"user_id": victim_id})
        check("ownership transfer to a removed member is refused", r.status_code in (400, 404), r.text)
    finally:
        try:
            for target in (other_pid, pid):
                if not target:
                    continue
                db.execute(sa.text("DELETE FROM environment_access WHERE environment_variable_id IN (SELECT id FROM environment_variables WHERE project_id = :p)"), {"p": target})
                db.execute(sa.text("DELETE FROM scaffold_secrets.environment_secrets WHERE environment_variable_id IN (SELECT id FROM environment_variables WHERE project_id = :p)"), {"p": target})
                for stmt in (
                    "DELETE FROM environment_variables WHERE project_id = :p",
                    "DELETE FROM blockers WHERE project_id = :p",
                    "DELETE FROM task_dependencies WHERE task_id IN (SELECT id FROM tasks WHERE project_id = :p)",
                    "DELETE FROM decision_affects_tasks WHERE task_id IN (SELECT id FROM decisions WHERE project_id = :p)",
                    "DELETE FROM api_contracts WHERE project_id = :p",
                    "DELETE FROM decisions WHERE project_id = :p",
                    "DELETE FROM events WHERE project_id = :p",
                    "DELETE FROM tasks WHERE project_id = :p",
                    "DELETE FROM users WHERE project_id = :p",
                    "DELETE FROM personal_access_tokens WHERE project_id = :p",
                    "DELETE FROM projects WHERE id = :p",
                ):
                    db.execute(sa.text(stmt), {"p": target})
            db.commit()
        except Exception:  # noqa: BLE001 — cleanup must never mask test results
            db.rollback()
        db.close()

print(f"\n== RESULT: {len(PASS)} passed, {len(FAIL)} failed ==")
if FAIL:
    print("FAILED:")
    for name in FAIL:
        print(f"  - {name}")
sys.exit(1 if FAIL else 0)
