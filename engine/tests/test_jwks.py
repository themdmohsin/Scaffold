"""JWKS / asymmetric Supabase JWT verification.

Run from engine/:  python -m tests.test_jwks

Section 1 is pure (no DB, no network): ES256 tokens are signed with locally
generated P-256 keys and verified against a MOCKED JWKS fetcher - the live
Supabase endpoint is never contacted. Section 2 drives the real app over
TestClient with ES256 session tokens (skipped loudly without DATABASE_URL) to
prove PATs, roles, project isolation and the /mcp gate still behave.
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

import tests.auth_helper as auth  # noqa: E402

import jwt as pyjwt  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.config import settings  # noqa: E402
from app.services import auth as auth_service  # noqa: E402
from app.services import jwks as jwks_service  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))


# --- fixtures ---------------------------------------------------------------
ISS = f"{auth.TEST_SUPABASE_URL}/auth/v1"
URL = jwks_service.jwks_url_for(auth.TEST_SUPABASE_URL)
KID_A, KID_B, KID_C = "kid-current", "kid-previous", "kid-rotated-in"


def _keypair():
    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    jwk = json.loads(pyjwt.algorithms.ECAlgorithm.to_jwk(priv.public_key()))
    return pem, jwk


PEM_A, JWK_A = _keypair()
PEM_B, JWK_B = _keypair()
PEM_C, JWK_C = _keypair()
PEM_ROGUE, _ = _keypair()


def _entry(jwk: dict, kid: str) -> dict:
    return {**jwk, "kid": kid, "alg": "ES256", "use": "sig", "key_ops": ["verify"]}


class MockJwks:
    """Stands in for the Supabase endpoint; counts fetches, can fail, can rotate."""

    def __init__(self) -> None:
        self.keys = [_entry(JWK_A, KID_A), _entry(JWK_B, KID_B)]
        self.calls = 0
        self.fail = False

    def __call__(self, url: str) -> dict:
        assert url == URL, url
        self.calls += 1
        if self.fail:
            raise RuntimeError("endpoint down")
        return {"keys": list(self.keys)}


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def es_token(pem: bytes = PEM_A, kid: str | None = KID_A, **over) -> str:
    now = int(time.time())
    payload = {
        "sub": str(uuid.uuid4()),
        "aud": "authenticated",
        "iss": ISS,
        "iat": now,
        "exp": now + 1800,
        "email": "es256@test.example",
        "role": "authenticated",
    }
    payload.update(over)
    headers = {"kid": kid} if kid else {}
    return pyjwt.encode(payload, pem, algorithm="ES256", headers=headers)


def verify(token: str) -> tuple[int, str]:
    try:
        sub, _ = auth_service._verify_jwt(token)
        return 200, sub
    except HTTPException as exc:
        return exc.status_code, str(exc.detail)
    except auth_service.AuthUnavailable:
        return 503, "unavailable"


def fresh(mock: MockJwks | None = None, clock: Clock | None = None):
    m = mock or MockJwks()
    c = clock or Clock()
    jwks_service.cache = jwks_service.JwksCache(fetch=m, clock=c)
    return m, c


print("\n== ES256 verification against a mocked JWKS ==")
mock, clock = fresh()
check("A. valid ES256 token, current key -> accepted", verify(es_token())[0] == 200)
check("B. valid ES256 token, the OTHER published key -> accepted", verify(es_token(PEM_B, KID_B))[0] == 200)
check("C. signature by a key not in the JWKS (kid of a real key) -> 401", verify(es_token(PEM_ROGUE, KID_A))[0] == 401)
check("C2. tampered payload -> 401", verify(es_token().rsplit(".", 2)[0] + "." + es_token().split(".")[1] + "." + es_token().split(".")[2])[0] == 401)
check("E. wrong issuer -> 401 issuer mismatch", verify(es_token(iss="https://evil.example/auth/v1")) == (401, "token issuer mismatch"))
check("F. wrong audience -> 401 audience mismatch", verify(es_token(aud="anon")) == (401, "token audience mismatch"))
check("G. expired token -> 401", verify(es_token(exp=int(time.time()) - 120))[0] == 401)
check("not-yet-valid (nbf in the future) -> 401", verify(es_token(nbf=int(time.time()) + 3600))[0] == 401)
check("missing exp -> 401", verify(pyjwt.encode({"sub": "x", "aud": "authenticated", "iss": ISS}, PEM_A, algorithm="ES256", headers={"kid": KID_A}))[0] == 401)
check("ES256 token without kid -> 401", verify(es_token(kid=None))[0] == 401)
check("a token only 5s expired is inside the clock-skew leeway", verify(es_token(exp=int(time.time()) - 5))[0] == 200)

print("\n== algorithm confusion / downgrade ==")
pub_pem = serialization.load_pem_private_key(PEM_A, None).public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
)
now = int(time.time())
forged = pyjwt.encode({"sub": "x", "aud": "authenticated", "iss": ISS, "exp": now + 600}, "x" * 48, algorithm="HS256", headers={"kid": KID_A})
check("HS256 token naming a JWKS kid is NOT verified with the public key -> 401", verify(forged)[0] == 401)
none_tok = pyjwt.encode({"sub": "x", "aud": "authenticated", "iss": ISS, "exp": now + 600}, None, algorithm="none")
check("alg=none -> 401", verify(none_tok)[0] == 401)
check("garbage token -> 401", verify("not.a.jwt")[0] == 401)
check("the legacy HS256 path still verifies with SUPABASE_JWT_SECRET", verify(auth.jwt_for(auth.OWNER_SUB))[0] == 200)

print("\n== cache, rotation, outage ==")
mock, clock = fresh()
for _ in range(5):
    verify(es_token())
verify(es_token(PEM_B, KID_B))
check("16. repeated verifications hit the JWKS endpoint exactly once", mock.calls == 1, str(mock.calls))

clock.t += jwks_service.JWKS_TTL_SECONDS + 1
verify(es_token())
check("cache refreshes after the TTL", mock.calls == 2, str(mock.calls))

# Rotation: a new key appears at the endpoint; first sight of its kid refreshes.
mock.keys.append(_entry(JWK_C, KID_C))
clock.t += jwks_service.JWKS_MIN_REFRESH_SECONDS + 1
check("17. rotated-in key (unknown kid) is picked up by an immediate refresh", verify(es_token(PEM_C, KID_C))[0] == 200)
check("rotation refresh fetched once", mock.calls == 3, str(mock.calls))

# Unknown kid: refresh attempted, still unknown -> 401; and flooding is rate limited.
mock, clock = fresh()
verify(es_token())  # prime
before = mock.calls
clock.t += jwks_service.JWKS_MIN_REFRESH_SECONDS + 1
check("D. unknown kid -> 401 after a refresh attempt", verify(es_token(PEM_A, "no-such-kid"))[0] == 401)
check("D. the unknown kid triggered exactly one refresh", mock.calls == before + 1, f"{before}->{mock.calls}")
for i in range(20):
    verify(es_token(PEM_A, f"bogus-{i}"))
check("a flood of bogus kids does not become a fetch flood (cooldown)", mock.calls == before + 1, str(mock.calls))
check("known keys keep working through the flood", verify(es_token())[0] == 200)

# Outage handling.
mock, clock = fresh()
mock.fail = True
check("18. JWKS unreachable and nothing cached -> 503 (fail closed)", verify(es_token())[0] == 503)
mock, clock = fresh()
verify(es_token())
mock.fail = True
clock.t += jwks_service.JWKS_TTL_SECONDS + 1
check("outage after a good fetch: cached keys keep verifying", verify(es_token())[0] == 200)
clock.t += jwks_service.JWKS_MAX_STALE_SECONDS + 1
check("outage beyond the stale limit -> 503", verify(es_token())[0] == 503)

mock, clock = fresh()
mock.keys = [{"kty": "EC", "kid": "broken"}]
check("a JWKS with no usable keys -> 503, never trust", verify(es_token())[0] == 503)

_orig_url = settings.supabase_url
try:
    settings.supabase_url = ""
    fresh()
    check("ES256 with SUPABASE_URL unset -> 503", verify(es_token())[0] == 503)
finally:
    settings.supabase_url = _orig_url

_orig_secret = settings.supabase_jwt_secret
try:
    settings.supabase_jwt_secret = ""
    fresh()
    check("JWKS-only config (no SUPABASE_JWT_SECRET): ES256 still verifies", verify(es_token())[0] == 200)
    check("JWKS-only config: an HS256 token is refused with 503", verify(auth.jwt_for(auth.OWNER_SUB))[0] == 503)
finally:
    settings.supabase_jwt_secret = _orig_secret

check("JWKS URL is derived from SUPABASE_URL", URL == f"{auth.TEST_SUPABASE_URL}/auth/v1/.well-known/jwks.json")

# --- routes -----------------------------------------------------------------
print("\n== routes with ES256 session tokens (skipped without DATABASE_URL) ==")
if not (settings.database_url or "").strip():
    print("  SKIP  DATABASE_URL not set on this machine")
else:
    from starlette.testclient import TestClient  # noqa: E402

    from app.main import app  # noqa: E402

    fresh()

    def client(token: str | None = None) -> TestClient:
        c = TestClient(app, raise_server_exceptions=False)
        if token:
            c.headers.update(auth.auth_headers(token))
        return c

    def es_for(sub: str, **over) -> str:
        return es_token(sub=sub, email=f"{sub[:8]}@test.example", **over)

    owner = client(es_for(auth.OWNER_SUB))
    outsider = client(es_for(auth.OUTSIDER_SUB, ))
    pid = other = None
    try:
        r = owner.get("/auth/me")
        check("ES256 session -> GET /auth/me 200 via=jwt", r.status_code == 200 and r.json()["via"] == "jwt", r.text[:200])
        r = owner.post("/projects", json={"name": f"jwks-test-{uuid.uuid4().hex[:8]}"})
        check("ES256 session can create a project (becomes owner)", r.status_code == 201, r.text[:200])
        pid = r.json()["id"]
        r = outsider.post("/projects", json={"name": f"jwks-other-{uuid.uuid4().hex[:8]}"})
        other = r.json()["id"]

        # K. cross-project isolation
        check("K. ES256 outsider is refused (403) on another project", outsider.get(f"/projects/{pid}/tasks").status_code == 403)
        check("K. owner is refused (403) on the outsider's project", owner.get(f"/projects/{other}/tasks").status_code == 403)
        check("K. no token -> 401", client().get(f"/projects/{pid}/tasks").status_code == 401)
        check("a bad-signature ES256 token -> 401 on a route", client(es_token(PEM_ROGUE, KID_A, sub=auth.OWNER_SUB)).get("/auth/me").status_code == 401)
        check("an unknown-kid ES256 token -> 401 on a route", client(es_token(kid="nope", sub=auth.OWNER_SUB)).get("/auth/me").status_code == 401)

        # I. roles
        auth.set_role(pid, auth.MEMBER_SUB, "member")
        auth.set_role(pid, auth.ADMIN_SUB, "admin")
        member = client(es_for(auth.MEMBER_SUB))
        admin = client(es_for(auth.ADMIN_SUB))
        check("I. member can read tasks", member.get(f"/projects/{pid}/tasks").status_code == 200)
        check("I. member cannot create invites (role too low)", member.post(f"/projects/{pid}/invite", json={}).status_code == 403)
        check("I. admin can create invites", admin.post(f"/projects/{pid}/invite", json={}).status_code == 201)

        # H. PATs unchanged
        r = owner.post("/auth/tokens", json={"name": "jwks-test", "project_id": pid})
        check("H. ES256 session mints a PAT", r.status_code == 201, r.text[:200])
        pat, pat_id = r.json()["token"], r.json()["id"]
        pc = client(pat)
        check("H. PAT authenticates (GET /auth/me via=pat)", pc.get("/auth/me").json().get("via") == "pat")
        check("H. PAT works on its own project", pc.get(f"/projects/{pid}/tasks").status_code == 200)
        check("H/K. PAT pinned to one project cannot touch another", pc.get(f"/projects/{other}/tasks").status_code == 403)
        check("H. a PAT cannot mint tokens", pc.post("/auth/tokens", json={"name": "x"}).status_code == 403)

        # J. MCP gate
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
        mh = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        check("J. /mcp without a credential -> 401", client().post("/mcp/", json=rpc, headers=mh).status_code == 401)
        check("J. /mcp with a forged ES256 token -> 401", client(es_token(PEM_ROGUE, KID_A)).post("/mcp/", json=rpc, headers=mh).status_code == 401)
        check("J. /mcp accepts a valid ES256 session token (passes the gate)", client(es_for(auth.OWNER_SUB)).post("/mcp/", json=rpc, headers=mh).status_code not in (401, 403, 503))
        check("J. /mcp accepts a PAT (passes the gate)", client(pat).post("/mcp/", json=rpc, headers=mh).status_code not in (401, 403, 503))

        r = owner.delete(f"/auth/tokens/{pat_id}")
        check("H. PAT revocation still takes effect immediately", r.status_code == 200 and pc.get("/auth/me").status_code == 401)
    finally:
        pass

print(f"\n== RESULT: {len(PASS)} passed, {len(FAIL)} failed ==")
if FAIL:
    for f in FAIL:
        print("  FAILED:", f)
    sys.exit(1)
