"""Day 2 verification: diff_parser unit tests + signed webhook e2e + all 6 MCP tools.

Run from engine/:  python -m tests.test_day2
No external network: GitHub REST is stubbed; MCP tools run over REAL streamable-HTTP
against a real uvicorn server (the exact production path, /mcp mount + lifespan).
"""

import hashlib
import hmac
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-secret")

PASS = []
FAIL = []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))


# ---------------------------------------------------------------------------
# 1. diff_parser unit tests
# ---------------------------------------------------------------------------
print("\n== diff_parser ==")
from app.services.diff_parser import parse_diff  # noqa: E402

SAMPLE_DIFF = """diff --git a/engine/app/routes/payments.py b/engine/app/routes/payments.py
index 111..222 100644
--- a/engine/app/routes/payments.py
+++ b/engine/app/routes/payments.py
@@ -1,3 +1,6 @@
+@router.post("/api/payments/checkout")
+def checkout():
+    pass
+
-def old_thing():
-    pass
diff --git a/dashboard/src/api.ts b/dashboard/src/api.ts
--- a/dashboard/src/api.ts
+++ b/dashboard/src/api.ts
@@ -0,0 +1,2 @@
+app.get("/api/payments/status", handler)
+router.delete("/api/payments/checkout", handler)
diff --git a/engine/requirements.txt b/engine/requirements.txt
--- a/engine/requirements.txt
+++ b/engine/requirements.txt
@@ -1,2 +1,4 @@
 fastapi>=0.115
+litellm>=1.50
+# a comment line that must be ignored
diff --git a/engine/.env.example b/engine/.env.example
--- a/engine/.env.example
+++ b/engine/.env.example
@@ -1,2 +1,4 @@
 DATABASE_URL=
+STRIPE_SECRET_KEY=
+old_key_removed=  must not count
"""

s = parse_diff(SAMPLE_DIFF)
check("files changed counted", s.files_changed == [
    "engine/app/routes/payments.py",
    "dashboard/src/api.ts",
    "engine/requirements.txt",
    "engine/.env.example",
], str(s.files_changed))
check("fastapi POST route found", {"route": "/api/payments/checkout", "method": "POST", "file": "engine/app/routes/payments.py"} in s.routes_added, str(s.routes_added))
check("express GET route found", {"route": "/api/payments/status", "method": "GET", "file": "dashboard/src/api.ts"} in s.routes_added, str(s.routes_added))
check("express DELETE route found", {"route": "/api/payments/checkout", "method": "DELETE", "file": "dashboard/src/api.ts"} in s.routes_added, str(s.routes_added))
check("removed lines produce no routes", all("old_thing" not in r["route"] for r in s.routes_added))
check("pypi dep found", {"name": "litellm", "version_spec": ">=1.50", "ecosystem": "pypi", "file": "engine/requirements.txt"} in s.dependencies_added, str(s.dependencies_added))
check("dep comment ignored", all(d["name"] != "a" for d in s.dependencies_added))
check("env key found", {"key": "STRIPE_SECRET_KEY", "file": "engine/.env.example"} in s.env_keys_added, str(s.env_keys_added))
check("context lines are not env keys", {"key": "DATABASE_URL", "file": "engine/.env.example"} not in s.env_keys_added)

neg = parse_diff("this is not a diff at all\njust text\n")
check("garbage input yields empty summary", neg.is_empty and neg.files_changed == [])

# ---------------------------------------------------------------------------
# 2. Signed webhook e2e (GitHub REST stubbed) against the real app + real DB
# ---------------------------------------------------------------------------
print("\n== webhook e2e ==")
from starlette.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.db.session import _init  # noqa: E402
from app.routes import github_webhook as hook  # noqa: E402

SECRET = "test-secret"
client = TestClient(app, raise_server_exceptions=False)

db = _init()()
from app.db.models import ApiContract, Commit, Event, Project, User  # noqa: E402

proj = Project(name=f"day2-test-{uuid.uuid4().hex[:6]}", goal="webhook e2e")
db.add(proj)
db.flush()
db.add(User(project_id=proj.id, name="tester"))
db.commit()
PID = str(proj.id)
db.close()

COMMIT_SHA = "abc123def4567890abcdef1234567890abcdef12"
STUB_COMMIT = {
    "sha": COMMIT_SHA,
    "commit": {"message": "add payments routes", "author": {"name": "Mohammed"}},
    "files": [
        {
            "filename": "engine/app/routes/payments.py",
            "patch": "@@ -1,2 +1,4 @@\n+@router.post(\"/api/payments/checkout\")\n+def checkout(): pass\n",
        },
        {
            "filename": "engine/requirements.txt",
            "patch": "@@ -1,2 +1,3 @@\n+litellm>=1.50\n",
        },
    ],
}


class _StubResponse:
    status_code = 200

    def raise_for_status(self) -> None: ...

    def json(self) -> dict:
        return STUB_COMMIT


async def _stub_get(self, url, **kwargs):  # noqa: ANN001
    return _StubResponse()


orig_get = hook.httpx.AsyncClient.get
hook.httpx.AsyncClient.get = _stub_get


def signed(body: bytes, secret: str = SECRET) -> dict:
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": f"sha256={sig}",
        "X-GitHub-Event": "push",
        "Content-Type": "application/json",
    }


url = f"/projects/{PID}/github-webhook"
body = json.dumps({
    "repository": {"full_name": "themdmohsin/Scaffold"},
    "commits": [{"id": COMMIT_SHA, "message": "add payments routes"}],
}).encode()

r = client.post(url, content=body, headers=signed(body))
check("signed push accepted", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
proc = r.json().get("processed", [{}])[0]
check(
    "commit processed with findings (hunk-only patch classified)",
    proc.get("routes") == 1 and proc.get("deps") == 1,
    str(proc),
)

branch_body = json.dumps({
    "repository": {"full_name": "themdmohsin/Scaffold"},
    "ref": "refs/heads/feature/wip-auth",
    "commits": [{"id": COMMIT_SHA, "message": "WIP routes on a branch"}],
}).encode()
r_branch = client.post(url, content=branch_body, headers=signed(branch_body))
check(
    "feature-branch push acknowledged but NOT ingested (WIP routes must not become contracts)",
    r_branch.status_code == 200 and r_branch.json().get("processed") == [] and "skipped" in r_branch.json(),
    r_branch.text[:160],
)

r_bad = client.post(url, content=body, headers={**signed(body), "X-Hub-Signature-256": "sha256=" + "0" * 64})
check("bad signature rejected 403", r_bad.status_code == 403, str(r_bad.status_code))

r_none = client.post(url, content=body, headers={"X-GitHub-Event": "push"})
check("missing signature rejected 400", r_none.status_code == 400, str(r_none.status_code))

r_wrong = client.post(url, content=body, headers=signed(body, secret="other-secret"))
check("wrong secret rejected 403", r_wrong.status_code == 403, str(r_wrong.status_code))

ping_body = b"{}"
r_ping = client.post(
    url,
    content=ping_body,
    headers={
        "X-Hub-Signature-256": "sha256=" + hmac.new(SECRET.encode(), ping_body, hashlib.sha256).hexdigest(),
        "X-GitHub-Event": "ping",
    },
)
check("ping answered", r_ping.status_code == 200 and r_ping.json().get("pong") is True, r_ping.text[:100])

db = _init()()
commit_row = db.query(Commit).filter(Commit.project_id == uuid.UUID(PID)).first()
check("commit row written", commit_row is not None and commit_row.sha == COMMIT_SHA and commit_row.author == "Mohammed")
contract_row = db.query(ApiContract).filter(ApiContract.project_id == uuid.UUID(PID)).first()
check(
    "api_contract row written from diff",
    contract_row is not None and contract_row.route == "/api/payments/checkout" and contract_row.method == "POST",
)
ev = db.query(Event).filter(Event.project_id == uuid.UUID(PID), Event.type == "commit_ingested").first()
check("commit_ingested event written", ev is not None and ev.payload["sha"] == COMMIT_SHA[:10])
db.close()

hook.httpx.AsyncClient.get = orig_get

# ---------------------------------------------------------------------------
# 3. All 6 MCP tools over REAL streamable-HTTP (uvicorn + JSON-RPC client)
# ---------------------------------------------------------------------------
print("\n== mcp tools (real server, real protocol) ==")
import httpx  # noqa: E402
import uvicorn  # noqa: E402

HOST, PORT = "127.0.0.1", 8971
server_config = uvicorn.Config(app, host=HOST, port=PORT, log_level="error")
server = uvicorn.Server(server_config)
thread = threading.Thread(target=server.run, daemon=True)
thread.start()

for _ in range(50):
    try:
        if httpx.get(f"http://{HOST}:{PORT}/health", timeout=2).status_code == 200:
            break
    except Exception:
        time.sleep(0.2)
else:
    print("FAIL  server did not start")
    sys.exit(1)
print("  uvicorn up at", f"http://{HOST}:{PORT}")

BASE = f"http://{HOST}:{PORT}/mcp"
_next_id = 0


def rpc(http: httpx.Client, method: str, params: dict | None = None, *, session_id: str | None = None, notify: bool = False):
    global _next_id
    _next_id += 1
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if not notify:
        msg["id"] = _next_id
    if params is not None:
        msg["params"] = params
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if session_id:
        headers["mcp-session-id"] = session_id
    res = http.post(BASE, json=msg, headers=headers, timeout=20, follow_redirects=True)
    sid = res.headers.get("mcp-session-id") or session_id
    if notify or res.status_code == 202:
        return None, sid
    result = None
    if "text/event-stream" in res.headers.get("content-type", ""):
        for line in res.text.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if not payload:
                    continue
                m = json.loads(payload)
                if m.get("id") == msg.get("id"):
                    result = m
                    break
    else:
        result = res.json()
    if result is None or "error" in result:
        raise RuntimeError(f"{method} failed: {res.status_code} {res.text[:300]}")
    return result["result"], sid


def tool_result(rpc_result: dict):
    """Extract the parsed JSON payload from a tools/call result."""
    content = rpc_result.get("content") or []
    if content and content[0].get("type") == "text":
        return json.loads(content[0]["text"])
    if "structuredContent" in rpc_result:
        return rpc_result["structuredContent"]
    raise RuntimeError(f"unexpected tool result shape: {str(rpc_result)[:200]}")


with httpx.Client() as http:
    init, sid = rpc(http, "initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "scaffold-day2-test", "version": "0.0.1"},
    })
    check("mcp initialize handshake", init.get("serverInfo", {}).get("name") == "scaffold", str(init)[:150])
    rpc(http, "notifications/initialized", session_id=sid, notify=True)

    tools, _ = rpc(http, "tools/list", session_id=sid)
    names = {t["name"] for t in tools["tools"]}
    expected = {
        "get_project_context",
        "get_api_contract",
        "get_active_tasks",
        "get_recent_decisions",
        "report_change",
        "create_task",
    }
    check("all 6 frozen tool names exposed", expected <= names, str(expected - names))

    r, _ = rpc(http, "tools/call", {"name": "get_project_context", "arguments": {"project_id": PID}}, session_id=sid)
    ctx = tool_result(r)
    check("get_project_context returns project", ctx.get("project", {}).get("id") == PID, str(ctx)[:150])

    r, _ = rpc(http, "tools/call", {"name": "get_api_contract", "arguments": {"route": "/api/payments/checkout", "project_id": PID}}, session_id=sid)
    c = tool_result(r)
    check("get_api_contract finds the ingested contract", c.get("found") is True and c.get("method") == "POST", str(c)[:150])

    r, _ = rpc(http, "tools/call", {"name": "get_api_contract", "arguments": {"route": "/nope", "project_id": PID}}, session_id=sid)
    check("get_api_contract miss -> found=false", tool_result(r).get("found") is False)

    r, _ = rpc(http, "tools/call", {"name": "get_active_tasks", "arguments": {"project_id": PID}}, session_id=sid)
    at = tool_result(r)
    check("get_active_tasks returns tasks object", isinstance(at, dict) and isinstance(at.get("tasks"), list) and "count" in at, str(at)[:120])

    r, _ = rpc(http, "tools/call", {"name": "get_recent_decisions", "arguments": {"project_id": PID, "limit": 3}}, session_id=sid)
    rd = tool_result(r)
    check("get_recent_decisions returns decisions object", isinstance(rd, dict) and isinstance(rd.get("decisions"), list) and "count" in rd, str(rd)[:120])

    r, _ = rpc(http, "tools/call", {"name": "report_change", "arguments": {"diff_summary": "added payments checkout endpoint", "files_changed": ["engine/app/routes/payments.py"], "project_id": PID}}, session_id=sid)
    check("report_change writes event", tool_result(r).get("ok") is True)

    r, _ = rpc(http, "tools/call", {"name": "create_task", "arguments": {"title": "mcp-created task", "project_id": PID}}, session_id=sid)
    created = tool_result(r)
    check("create_task creates", created.get("status") == "todo", str(created))

server.should_exit = True
thread.join(timeout=10)

db = _init()()
ev = (
    db.query(Event)
    .filter(Event.project_id == uuid.UUID(PID), Event.type == "change_reported")
    .first()
)
check("change_reported event persisted via MCP", ev is not None and ev.payload["diff_summary"] == "added payments checkout endpoint")
mcp_task = (
    db.query(Event)
    .filter(Event.project_id == uuid.UUID(PID), Event.type == "task_created", Event.payload["via"].astext == "mcp")
    .first()
)
check("mcp create_task event persisted", mcp_task is not None)
db.close()

# ---------------------------------------------------------------------------
print(f"\n== RESULT: {len(PASS)} passed, {len(FAIL)} failed ==")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
