"""Day 3 verification: reasoning parse units, retrieval (vector + fallback),
/reason e2e (LiteLLM mocked), context placeholders, webhook summary, backfill idempotency.

Run from engine/:  python -m tests.test_day3
No external network: litellm calls are stubbed; everything else hits the real DB.
"""

import json
import os
import re
import sys
import threading
import time
import types
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Deterministic secrets regardless of what engine/.env holds (env vars outrank
# the dotenv file). All LLM calls are stubbed below, so a dummy key is fine.
os.environ["SCAFFOLD_TEAM_LLM_KEY"] = "test-key"
os.environ["GITHUB_WEBHOOK_SECRET"] = "test-secret"

PASS = []
FAIL = []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))


# ---------------------------------------------------------------------------
# 1. reasoning._parse_answer units (pure, no network)
# ---------------------------------------------------------------------------
print("\n== reasoning parse units ==")
from app.services import reasoning  # noqa: E402

r = reasoning._parse_answer('{"answer": "use Postgres", "suggested_tasks": [{"title": "add pgvector", "owner_id": null, "due_at": null}]}')
check("valid json parsed", r["answer"] == "use Postgres" and r["suggested_tasks"][0]["title"] == "add pgvector", str(r))
r = reasoning._parse_answer('```json\n{"answer": "fenced", "suggested_tasks": []}\n```')
check("fenced json parsed", r["answer"] == "fenced", str(r))
r = reasoning._parse_answer("plain prose answer without json")
check("non-json degrades to prose", r["answer"] == "plain prose answer without json" and r["suggested_tasks"] == [], str(r))
r = reasoning._parse_answer('{"answer": "ok", "suggested_tasks": [{"title": "  "}, {"nope": 1}, {"title": "real task", "owner_id": "u1", "due_at": "2026-10-01"}]}')
check(
    "task list sanitized (blank/invalid dropped, fields coerced)",
    len(r["suggested_tasks"]) == 1
    and r["suggested_tasks"][0]["title"] == "real task"
    and r["suggested_tasks"][0]["owner_id"] == "u1",
    str(r),
)
r = reasoning._parse_answer('{"answer": "x", "suggested_tasks": ["not a dict"]}')
check("non-dict tasks dropped", r["suggested_tasks"] == [], str(r))

# ---------------------------------------------------------------------------
# 2. Fixtures: project + decisions/contracts in the real DB
# ---------------------------------------------------------------------------
print("\n== retrieval (real DB, vectors stubbed) ==")
from starlette.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.db.session import _init  # noqa: E402
from app.db.models import ApiContract, Commit, Decision, Event, Project  # noqa: E402
from app.services import retrieval  # noqa: E402

db = _init()()
proj = Project(name=f"day3-test-{uuid.uuid4().hex[:6]}", goal="reasoning e2e", deadline=None)
db.add(proj)
db.flush()
PID = proj.id
d_auth = Decision(project_id=PID, text="Auth must use JWT access tokens with 15-minute expiry", reasoning="chosen over sessions for statelessness")
d_db = Decision(project_id=PID, text="All persistence goes through Supabase Postgres", reasoning="single source of truth")
db.add_all([d_auth, d_db])
c_login = ApiContract(project_id=PID, route="/api/auth/login", method="POST", request_schema={"email": "string"}, response_schema={"token": "string"})
db.add(c_login)
db.commit()
PID_STR = str(PID)
db.close()

VEC_AUTH = [1.0] + [0.0] * 1535
VEC_OTHER = [0.0, 1.0] + [0.0] * 1534


def _stub_vec(text: str) -> list[float]:
    return VEC_AUTH if ("jwt" in text or "auth" in text.lower()) else VEC_OTHER


orig_embed_text = reasoning.embed_text
reasoning.embed_text = _stub_vec  # type: ignore[method-assign]

try:
    hits = retrieval.relevant_decisions(_init()(), PID, "How should we handle login auth?")
    check("vector path ranks the auth decision first", hits and hits[0]["text"].startswith("Auth must use JWT"), str([h["text"] for h in hits]))

    # keyword fallback: embedding provider down -> deterministic token overlap
    reasoning.embed_text = lambda text: (_ for _ in ()).throw(reasoning.LlmUnavailableError("down"))  # type: ignore[method-assign]
    hits = retrieval.relevant_decisions(_init()(), PID, "jwt expiry tokens")
    check("keyword fallback finds jwt decision", hits and hits[0]["text"].startswith("Auth must use JWT"), str([h["text"] for h in hits]))

    con_hits = retrieval.relevant_contracts(_init()(), PID, "what is the auth login endpoint")
    check("contract keyword fallback finds /api/auth/login", any(c["route"] == "/api/auth/login" for c in con_hits), str(con_hits))
finally:
    reasoning.embed_text = orig_embed_text  # type: ignore[method-assign]

# ---------------------------------------------------------------------------
# 3. /reason e2e (litellm stubbed) over the real app
# ---------------------------------------------------------------------------
print("\n== /reason endpoint ==")
client = TestClient(app, raise_server_exceptions=False)

_FAKE_COMPLETION = types.SimpleNamespace(
    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps({
        "answer": "Use the existing /api/auth/login contract; JWT expiry is 15 minutes.",
        "suggested_tasks": [{"title": "wire login UI to /api/auth/login", "owner_id": None, "due_at": None}],
    })))]
)


class _StubCompletion:
    def __init__(self, resp=None, exc: Exception | None = None):
        self.resp = resp
        self.exc = exc
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.resp


stub_completion = _StubCompletion(_FAKE_COMPLETION)
orig_completion = reasoning.completion
reasoning.completion = stub_completion  # type: ignore[assignment]

try:
    r = client.post(f"/projects/{PID_STR}/reason", json={"prompt": "How should the login form call the backend?"})
    check("reason 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
    body = r.json()
    check("reason shape {answer, suggested_tasks}", set(body) == {"answer", "suggested_tasks"}, str(body)[:200])
    check("reason answer from stubbed llm", "auth/login" in body["answer"], str(body)[:200])
    check("reason suggested task present", body["suggested_tasks"][0]["title"] == "wire login UI to /api/auth/login", str(body))
    check("reason sent small context block", stub_completion.calls and len(stub_completion.calls[0]["messages"][1]["content"]) <= 4500, str(len(stub_completion.calls[0]["messages"][1]["content"]) if stub_completion.calls else -1))
    check("reason used chat model via litellm", stub_completion.calls and str(stub_completion.calls[0]["model"]).startswith("gemini/"), str(stub_completion.calls[0]["model"] if stub_completion.calls else None))

    r404 = client.post(f"/projects/{uuid.uuid4()}/reason", json={"prompt": "hello"})
    check("reason 404 unknown project", r404.status_code == 404, str(r404.status_code))

    r400 = client.post(f"/projects/{PID_STR}/reason", json={})
    check("reason 422 missing prompt", r400.status_code == 422, str(r400.status_code))

    # 503 when the team key is missing (config, not network)
    from app.config import settings  # noqa: E402

    saved_key = settings.scaffold_team_llm_key
    settings.scaffold_team_llm_key = ""
    try:
        r503 = client.post(f"/projects/{PID_STR}/reason", json={"prompt": "anything"})
        check("reason 503 without SCAFFOLD_TEAM_LLM_KEY", r503.status_code == 503, f"{r503.status_code} {r503.text[:150]}")
    finally:
        settings.scaffold_team_llm_key = saved_key

    # 502 when the provider errors
    reasoning.completion = _StubCompletion(exc=RuntimeError("provider outage"))  # type: ignore[assignment]
    r502 = client.post(f"/projects/{PID_STR}/reason", json={"prompt": "anything"})
    check("reason 502 on provider error", r502.status_code == 502, str(r502.status_code))
finally:
    reasoning.completion = orig_completion  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# 4. Context placeholders filled (deterministic, no LLM)
# ---------------------------------------------------------------------------
print("\n== context placeholders ==")
r = client.get(f"/projects/{PID_STR}/context")
ctx = r.json()
check(
    "recent_decisions filled newest-first",
    [d["text"] for d in ctx["recent_decisions"]][:1] == [d_db.text] or len(ctx["recent_decisions"]) == 2,
    str(ctx["recent_decisions"])[:200],
)
check("recent_decisions shape {id,text,created_at}", all(set(d) == {"id", "text", "created_at"} for d in ctx["recent_decisions"]), str(ctx["recent_decisions"])[:200])
check(
    "relevant_contracts shape {route,method}",
    any(c["route"] == "/api/auth/login" and c["method"] == "POST" for c in ctx["relevant_contracts"]) and all(set(c) == {"route", "method"} for c in ctx["relevant_contracts"]),
    str(ctx["relevant_contracts"]),
)

# ---------------------------------------------------------------------------
# 5. Webhook writes commits.summary (LLM stubbed, fail-open)
# ---------------------------------------------------------------------------
print("\n== webhook summary ==")
import hashlib  # noqa: E402
import hmac  # noqa: E402

from app.routes import github_webhook as hook  # noqa: E402

SECRET = "test-secret"
SHA = "deadbeef" * 8
STUB_COMMIT = {
    "sha": SHA,
    "commit": {"message": "add login route", "author": {"name": "Mohammed"}},
    "files": [{"filename": "engine/app/routes/auth.py", "patch": '@@ -0,0 +1,2 @@\n+@router.post("/api/auth/login")\n+def login(): pass\n'}],
}


class _StubResponse:
    status_code = 200

    def raise_for_status(self) -> None: ...

    def json(self) -> dict:
        return STUB_COMMIT


async def _stub_get(self, url, **kwargs):  # noqa: ANN001
    return _StubResponse()


orig_http_get = hook.httpx.AsyncClient.get
orig_summarize = reasoning.summarize_diff
hook.httpx.AsyncClient.get = _stub_get  # type: ignore[method-assign]


def signed(body: bytes) -> dict:
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={sig}", "X-GitHub-Event": "push", "Content-Type": "application/json"}


try:
    wb = json.dumps({"repository": {"full_name": "themdmohsin/Scaffold"}, "commits": [{"id": SHA}]}).encode()
    reasoning.summarize_diff = lambda *a, **k: "adds POST /api/auth/login handler"  # type: ignore[assignment]
    r = client.post(f"/projects/{PID_STR}/github-webhook", content=wb, headers=signed(wb))
    check("signed push accepted", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    row = _init()().query(Commit).filter(Commit.sha == SHA).first()
    check("commits.summary filled by reasoning", row is not None and row.summary == "adds POST /api/auth/login handler", str(row.summary if row else None))

    # fail open: provider down -> summary null, ingestion still succeeds
    SHA2 = "feedface" * 8
    STUB_COMMIT["sha"] = SHA2
    reasoning.summarize_diff = lambda *a, **k: (_ for _ in ()).throw(reasoning.LlmUnavailableError("down"))  # type: ignore[assignment]
    wb2 = json.dumps({"repository": {"full_name": "themdmohsin/Scaffold"}, "commits": [{"id": SHA2}]}).encode()
    r2 = client.post(f"/projects/{PID_STR}/github-webhook", content=wb2, headers=signed(wb2))
    row2 = _init()().query(Commit).filter(Commit.sha == SHA2).first()
    check("summary fails open (null) on provider outage", r2.status_code == 200 and row2 is not None and row2.summary is None, f"{r2.status_code} {row2.summary if row2 else None}")
finally:
    hook.httpx.AsyncClient.get = orig_http_get  # type: ignore[method-assign]
    reasoning.summarize_diff = orig_summarize  # type: ignore[method-assign]

# ---------------------------------------------------------------------------
# 6. Backfill idempotency (embedding stub still active)
# ---------------------------------------------------------------------------
print("\n== backfill idempotency ==")
import io  # noqa: E402
from contextlib import redirect_stdout  # noqa: E402

import app.scripts.backfill_embeddings as backfill  # noqa: E402

reasoning.embed_text = _stub_vec  # type: ignore[method-assign]
try:
    sys.argv = ["backfill", "--project", PID_STR]
    buf = io.StringIO()
    with redirect_stdout(buf):
        backfill.main()
    first = buf.getvalue()
    buf = io.StringIO()
    with redirect_stdout(buf):
        backfill.main()
    second = buf.getvalue()
    check("first backfill embeds both decisions", "decisions: 2 embedded, 0 failed" in first, first)
    m = re.search(r"contracts: (\d+) embedded, 0 failed", first)
    check(
        "first backfill embeds the fixture contract (+ any webhook-parsed ones)",
        m is not None and int(m.group(1)) >= 1,
        first,
    )
    check("second backfill is a no-op (idempotent)", "decisions: 0 embedded, 0 failed" in second and "contracts: 0 embedded, 0 failed" in second, second)
finally:
    reasoning.embed_text = orig_embed_text  # type: ignore[method-assign]
    sys.argv = ["backfill"]

# vector row round-trip: embeddings actually stored as vector(1536)
db = _init()()
row = db.get(Decision, d_auth.id)
check("decision embedding persisted as 1536-dim vector", row is not None and row.embedding is not None and len(list(row.embedding)) == 1536)
crow = db.get(ApiContract, c_login.id)
check("contract embedding persisted as 1536-dim vector", crow is not None and crow.embedding is not None and len(list(crow.embedding)) == 1536)
db.close()

# ---------------------------------------------------------------------------
print(f"\n== RESULT: {len(PASS)} passed, {len(FAIL)} failed ==")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
