"""Day 4 Part B verification: deterministic conflict detection, recorder, auto-GitHub-issue.

Run from engine/:  python -m tests.test_day4b
No external network: GitHub issue calls are stubbed; LLM calls are stubbed;
route tests hit the real DB exactly like the Day 2/3 suites.
"""

import hashlib
import hmac
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Deterministic secrets regardless of what engine/.env holds (env vars outrank
# the dotenv file). GitHub issue calls are stubbed below, so no real token needed.
os.environ["GITHUB_WEBHOOK_SECRET"] = "test-secret"
os.environ["SCAFFOLD_GITHUB_REPO"] = "themdmohsin/Scaffold"

PASS = []
FAIL = []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))


# ---------------------------------------------------------------------------
# 1. Pure detection units — no DB, no network
# ---------------------------------------------------------------------------
print("\n== conflict detection units ==")
from app.services import conflict_service as cs  # noqa: E402

inc = {"route": "/api/auth/login", "method": "POST", "request_schema": {"email": "string"}, "response_schema": {"token": "string"}}
same = {"route": "/api/auth/login", "method": "POST", "request_schema": {"email": "string"}, "response_schema": {"token": "string"}}
divergent = {"route": "/api/auth/login", "method": "POST", "request_schema": {"username": "string"}, "response_schema": {"jwt": "string"}}
no_schema = {"route": "/api/auth/login", "method": "POST"}

# differing shape on same method+route -> conflict
r = cs.detect_contract_conflicts([divergent], [inc])
check("differing shape -> conflicting_shape", len(r) == 1 and r[0]["kind"] == "conflicting_shape" and r[0]["severity"] == "conflict", str(r))

# identical re-report -> NOT surfaced (info only)
r = cs.detect_contract_conflicts([same], [inc])
check("identical re-registration -> no finding", r == [], str(r))

# webhook case: neither side has schemas -> no shape claim, no finding
r = cs.detect_contract_conflicts([no_schema], [{"route": "/api/auth/login", "method": "POST"}])
check("no-schema pair -> no shape claim", r == [], str(r))

# one side has schemas, other doesn't (plugin reported vs webhook-registered) -> no shape claim
r = cs.detect_contract_conflicts([divergent], [no_schema])
check("schema vs no-schema -> no shape claim", r == [], str(r))

# method divergence on same path
r = cs.detect_contract_conflicts([{"route": "/api/auth/login", "method": "GET"}], [inc])
check("same path different method -> method_divergence", len(r) == 1 and r[0]["kind"] == "method_divergence", str(r))

# path-param styles normalize (:id == {id})
r = cs.detect_contract_conflicts(
    [{"route": "/api/users/{id}", "method": "POST"}],
    [{"route": "/api/users/:id", "method": "PUT"}],
)
check("param styles normalize -> method_divergence", len(r) == 1 and r[0]["kind"] == "method_divergence", str(r))

# sibling collision under the same feature prefix
r = cs.detect_contract_conflicts(
    [{"route": "/api/auth/magic-link", "method": "POST"}],
    [{"route": "/api/auth/login", "method": "POST", "request_schema": {"email": "string"}}],
)
check("same prefix different path -> sibling_collision", len(r) == 1 and r[0]["kind"] == "sibling_collision" and r[0]["severity"] == "warning", str(r))

# unrelated route is clean
r = cs.detect_contract_conflicts(
    [{"route": "/api/projects/list", "method": "GET"}],
    [{"route": "/api/auth/login", "method": "POST", "request_schema": {"email": "string"}}],
)
check("unrelated route -> no finding", r == [], str(r))

# severity ordering: conflict findings come before warnings
r = cs.detect_contract_conflicts(
    [divergent, {"route": "/api/auth/magic-link", "method": "POST"}],
    [inc, {"route": "/api/auth/login", "method": "POST", "request_schema": {"email": "string"}}],
)
check("findings sorted conflict-first", [f["severity"] for f in r] == ["conflict", "warning"], str(r))

# determinism: same input twice -> identical output
a = cs.detect_contract_conflicts([divergent], [inc])
b = cs.detect_contract_conflicts([divergent], [inc])
check("deterministic output", a == b, f"{a} != {b}")

# ---------------------------------------------------------------------------
# 2. github_issues units — stubbed httpx, no network
# ---------------------------------------------------------------------------
print("\n== github issue action ==")
from app.services import github_issues as gi  # noqa: E402

# Settings is instantiated at import time, so set the attribute directly.
gi.settings.github_token = "test-token"

finding = {
    "kind": "conflicting_shape",
    "severity": "conflict",
    "existing": {"route": "/api/auth/login", "method": "POST", "request_schema": {"email": "string"}, "response_schema": {"token": "string"}},
    "incoming": {"route": "/api/auth/login", "method": "POST", "request_schema": {"username": "string"}, "response_schema": {"jwt": "string"}},
    "differing_fields": ["request_schema.email", "request_schema.username"],
}

class _FakeResponse:
    def __init__(self, status_code=201, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
    def json(self):
        return self._payload
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

class _FakeIssueClient:
    """Mimics the httpx.AsyncClient call pattern in github_issues."""
    def __init__(self, existing_titles=None, fail=False):
        self.existing_titles = existing_titles or []
        self.fail = fail
        self.created = None
    async def __aenter__(self):
        return self
    async def __aexit__(self, *exc):
        return False
    async def post(self, url, headers=None, json=None, params=None):
        if "/labels" in url:
            return _FakeResponse(201)
        if self.fail:
            raise RuntimeError("github down")
        self.created = json
        return _FakeResponse(201, {"number": 7, "html_url": "https://github.com/x/issues/7", "title": json["title"]})
    async def get(self, url, headers=None, params=None):
        return _FakeResponse(200, [{"title": t, "state": "open"} for t in self.existing_titles])

_orig_client = gi.httpx.AsyncClient
try:
    fake = _FakeIssueClient()
    gi.httpx.AsyncClient = lambda *a, **k: fake  # type: ignore[assignment]
    import asyncio
    loop = asyncio.new_event_loop()
    result = loop.run_until_complete(gi.create_conflict_issue(finding, {"incoming_source": "test"}))
    loop.close()
    check("issue created with correct title", isinstance(result, dict) and result.get("number") == 7 and "[SCAFFOLD]" in (result.get("title") or ""), str(result))
    check("issue body lists both schemas", fake.created and "request_schema" in json.dumps(fake.created), str(fake.created)[:120] if fake.created else "none")

    fake2 = _FakeIssueClient(existing_titles=["[SCAFFOLD] Conflicting API contract: POST /api/auth/login"])
    gi.httpx.AsyncClient = lambda *a, **k: fake2  # type: ignore[assignment]
    loop = asyncio.new_event_loop()
    result2 = loop.run_until_complete(gi.create_conflict_issue(finding, {"incoming_source": "test"}))
    loop.close()
    check("dedupe: identical open issue -> skipped", result2.get("skipped") == "identical open issue already exists", str(result2))
finally:
    gi.httpx.AsyncClient = _orig_client  # type: ignore[assignment]

# no token -> skipped before any network call
gi.settings.github_token = ""
result3 = asyncio.new_event_loop()
out3 = result3.run_until_complete(gi.create_conflict_issue(finding))
result3.close()
check("no GITHUB_TOKEN -> skipped cleanly", out3 is not None and "skipped" in out3 and "GITHUB_TOKEN" in out3["skipped"], str(out3))

# spawn_issue_creation never raises, even with everything broken
gi.spawn_issue_creation({"kind": "x", "severity": "conflict", "incoming": {}, "existing": {}})
check("spawn_issue_creation never raises", True)

# ---------------------------------------------------------------------------
# 3. Route-level: POST /projects/:id/contracts fires detection (real DB)
# ---------------------------------------------------------------------------
print("\n== contracts API conflict wiring ==")
from starlette.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.db.session import _init  # noqa: E402
from app.db.models import ApiContract, Blocker, Event, Project  # noqa: E402
from app.services import github_issues as gi2  # noqa: E402

client = TestClient(app)
db = _init()()
proj = Project(name=f"day4b-test-{uuid.uuid4().hex[:6]}", goal="conflict e2e")
db.add(proj)
db.flush()
PID = proj.id
db.add(ApiContract(project_id=PID, route="/api/auth/login", method="POST", request_schema={"email": "string"}, response_schema={"token": "string"}))
db.commit()
PID_STR = str(PID)
db.close()

# stub the issue action at the recorder boundary — we assert events/blockers here,
# the issue action itself is covered in section 2.
gi2.spawn_issue_creation = lambda *a, **k: None  # type: ignore[assignment]

r = client.post(
    f"/projects/{PID_STR}/contracts",
    json={"route": "/api/auth/login", "method": "POST", "request_schema": {"username": "string"}, "response_schema": {"jwt": "string"}},
)
check("divergent contract still registers (201)", r.status_code == 201, f"{r.status_code} {r.text[:150]}")
check("response carries conflicts summary", isinstance(r.json().get("conflicts"), dict), r.text[:200])

db = _init()()
ev = db.query(Event).filter(Event.project_id == PID, Event.type == "conflict_flagged").all()
check("conflict_flagged event written", len(ev) >= 1 and ev[-1].payload.get("incoming", {}).get("route") == "/api/auth/login", str(ev[-1].payload if ev else None))
bl = db.query(Blocker).filter(Blocker.project_id == PID, Blocker.resolved.is_(False)).all()
check("blocker row written", len(bl) == 1 and "POST /api/auth/login" in (bl[0].description or ""), str([b.description for b in bl]))

# duplicate blocker suppression: same conflict again -> no second open blocker
r2 = client.post(
    f"/projects/{PID_STR}/contracts",
    json={"route": "/api/auth/login", "method": "POST", "request_schema": {"user": "string"}, "response_schema": {"jwt": "string"}},
)
bl2 = db.query(Blocker).filter(Blocker.project_id == PID, Blocker.resolved.is_(False)).all()
check("blocker dedupe on repeat conflict", len(bl2) == 1, str(len(bl2)))

# clean contract -> no new events
n_before = len(db.query(Event).filter(Event.project_id == PID, Event.type == "conflict_flagged").all())
r3 = client.post(f"/projects/{PID_STR}/contracts", json={"route": "/api/projects/other", "method": "GET"})
n_after = len(db.query(Event).filter(Event.project_id == PID, Event.type == "conflict_flagged").all())
check("clean contract -> no conflict events", r3.status_code == 201 and n_after == n_before, f"{r3.status_code} {n_before}->{n_after}")
db.close()

# ---------------------------------------------------------------------------
# 4. Webhook conflict wiring — stubbed GitHub REST (same technique as Day 3)
# ---------------------------------------------------------------------------
print("\n== webhook conflict wiring ==")
from app.routes import github_webhook as hook  # noqa: E402
from app.services import reasoning  # noqa: E402

SHA = "d4bda4c0" * 8
# A GET on an already-POSTed path — the classic method_divergence slip. (A same-method
# re-report is correctly a duplicate/info, so this stub deliberately diverges.)
PATCH = (
    "diff --git a/engine/app/routes/demo.py b/engine/app/routes/demo.py\n"
    "--- a/engine/app/routes/demo.py\n"
    "+++ b/engine/app/routes/demo.py\n"
    "+@router.get(\"/api/auth/login\")\n"
    "+def login_form(): ...\n"
)
STUB_COMMIT = {
    "files": [{"filename": "engine/app/routes/demo.py", "patch": PATCH}],
    "commit": {"message": "feat: agent B wires a GET login route", "author": {"name": "agent-b"}},
}


async def _stub_fetch(sha, repo):
    return (["engine/app/routes/demo.py"], STUB_COMMIT["commit"]["message"], "agent-b", PATCH)


def signed(body: bytes) -> dict:
    sig = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={sig}", "X-GitHub-Event": "push", "Content-Type": "application/json"}


orig_fetch = hook._fetch_commit_diff
orig_summ = reasoning.summarize_diff
try:
    hook._fetch_commit_diff = _stub_fetch  # type: ignore[assignment]
    reasoning.summarize_diff = lambda *a, **k: "adds GET /api/auth/login (method divergence)"  # type: ignore[assignment]

    wb = json.dumps({"repository": {"full_name": "themdmohsin/Scaffold"}, "commits": [{"id": SHA}]}).encode()
    r = client.post(f"/projects/{PID_STR}/github-webhook", content=wb, headers=signed(wb))
    check("signed push accepted", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    body = r.json()
    check("webhook reports conflict count", body.get("processed", [{}])[-1].get("conflicts") == 1, r.text[:200])

    db = _init()()
    ev = db.query(Event).filter(Event.project_id == PID, Event.type == "conflict_flagged").order_by(Event.created_at.desc()).all()
    latest = ev[0].payload if ev else {}
    check("webhook conflict_flagged event written", ev and latest.get("source", "").startswith("github push"), str(latest))
    check("webhook finding is method_divergence", latest.get("kind") == "method_divergence", str(latest))
    db.close()
finally:
    hook._fetch_commit_diff = orig_fetch  # type: ignore[assignment]
    reasoning.summarize_diff = orig_summ  # type: ignore[assignment]

# ---------------------------------------------------------------------------
print(f"\n== RESULT: {len(PASS)} passed, {len(FAIL)} failed ==")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
