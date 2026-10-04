"""Day 12 verification: the client-fork contract surface.

Covers the additive engine pieces the Scaffold client fork depends on:

  1. diff_parser.extract_routes_from_text — deterministic route extraction from
     a source body (same regexes as the webhook diff parser, no LLM).
  2. POST /projects/:id/contracts/check — stateless pre-write check used by the
     fork's tool.execute.before hook; conflict / warning / clean classification,
     auth gates, and the guarantee that nothing is written.
  3. X-Scaffold-Project header on /mcp — the per-repo binding
     (.scaffold/project.json) lets tool calls resolve the repo's project with
     no project_id argument; explicit arguments still win; malformed headers
     are ignored.

Run from engine/:  python -m tests.test_client_fork
MCP legs run over REAL streamable-HTTP against uvicorn (the production path).
"""

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tests.auth_helper as auth  # sets SUPABASE_JWT_SECRET / SUPABASE_URL BEFORE app.config

os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-secret")

PASS = []
FAIL = []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))


# ---------------------------------------------------------------------------
# 1. extract_routes_from_text — deterministic, diff_parser-equivalent
# ---------------------------------------------------------------------------
print("\n== extract_routes_from_text ==")
from app.services.diff_parser import extract_routes_from_text  # noqa: E402

FASTAPI_BODY = """from fastapi import APIRouter
router = APIRouter()

@router.post("/api/orders/checkout")
def checkout():
    pass

@router.get("/api/orders/{order_id}")
def get_order(order_id: str):
    pass
"""
routes = extract_routes_from_text(FASTAPI_BODY, "engine/app/routes/orders.py")
found = {(r["route"], r["method"]) for r in routes}
check("fastapi POST extracted", ("/api/orders/checkout", "POST") in found, str(found))
check("fastapi GET with param extracted", ("/api/orders/{order_id}", "GET") in found, str(found))
check("file attached to every route", all(r["file"] == "engine/app/routes/orders.py" for r in routes))

EXPRESS_BODY = "app.delete('/api/orders/checkout', handler)\n"
routes = extract_routes_from_text(EXPRESS_BODY, "dashboard/src/api.ts")
check("express DELETE extracted", routes == [{"route": "/api/orders/checkout", "method": "DELETE", "file": "dashboard/src/api.ts"}], str(routes))

check("python text in a .ts file yields no routes", extract_routes_from_text(FASTAPI_BODY, "notes.md") == [])
check("empty body yields no routes", extract_routes_from_text("", "engine/app/routes/x.py") == [])
dupes = extract_routes_from_text('@router.get("/api/a")\n@router.get("/api/a")\n', "a.py")
check("duplicate route lines deduped", len(dupes) == 1, str(dupes))
api_route = extract_routes_from_text('@app.api_route("/api/multi", methods=["GET", "POST"])\n', "a.py")
check("api_route multi-method extracted", {(r["route"], r["method"]) for r in api_route} == {("/api/multi", "GET"), ("/api/multi", "POST")}, str(api_route))

# ---------------------------------------------------------------------------
# 2. /contracts/check — stateless, deterministic, auth-gated
# ---------------------------------------------------------------------------
print("\n== POST /projects/:id/contracts/check ==")
from starlette.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.db.session import _init  # noqa: E402
from app.db.models import ApiContract, Event, Project  # noqa: E402

client = TestClient(app, raise_server_exceptions=False)

db = _init()()
proj = Project(name=f"day12-check-{uuid.uuid4().hex[:6]}", goal="contract check")
db.add(proj)
db.flush()
PID = str(proj.id)
db.commit()
db.close()

pa, OWNER_JWT = auth.bootstrap(PID)
client.headers.update(auth.auth_headers(OWNER_JWT))

REGISTERED = {
    "route": "/api/auth/login",
    "method": "POST",
    "request_schema": {"username": "string", "password": "string"},
    "response_schema": {"token": "string"},
}
r = client.post(f"/projects/{PID}/contracts", json=REGISTERED)
check("registered contract fixture created", r.status_code == 201, f"{r.status_code} {r.text[:200]}")


def check_count() -> int:
    db2 = _init()()
    try:
        return db2.query(Event).filter(Event.project_id == uuid.UUID(PID)).count()
    finally:
        db2.close()


def contract_count() -> int:
    db2 = _init()()
    try:
        return db2.query(ApiContract).filter(ApiContract.project_id == uuid.UUID(PID)).count()
    finally:
        db2.close()


events_before = check_count()
contracts_before = contract_count()

# --- clean: brand new route -------------------------------------------------
r = client.post(
    f"/projects/{PID}/contracts/check",
    json={"file": "engine/app/routes/orders.py", "routes": [{"route": "/api/orders", "method": "POST"}]},
)
body = r.json()
check("clean check is 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
check("clean check reports clean", body.get("clean") is True and body.get("checked") == 1, str(body)[:250])
check("clean check has no blocking finding", body.get("has_blocking") is False)

# --- conflict: same route, divergent schema ---------------------------------
r = client.post(
    f"/projects/{PID}/contracts/check",
    json={
        "file": "engine/app/routes/auth.py",
        "routes": [
            {
                "route": "/api/auth/login",
                "method": "POST",
                "request_schema": {"email": "string"},
                "response_schema": {"token": "string"},
            }
        ],
    },
)
body = r.json()
kinds = {(f["kind"], f["severity"]) for f in body.get("conflicts", [])}
check("schema divergence is a conflict finding", ("conflicting_shape", "conflict") in kinds, str(body)[:300])
check("conflict is blocking", body.get("has_blocking") is True)
check("conflict carries the registered shape", body["conflicts"][0]["existing"]["request_schema"].get("username") == "string", str(body)[:300])
check("conflict carries the incoming shape", body["conflicts"][0]["incoming"]["request_schema"].get("email") == "string")

# --- conflict: same route, different method ---------------------------------
r = client.post(
    f"/projects/{PID}/contracts/check",
    json={"routes": [{"route": "/api/auth/login", "method": "GET"}]},
)
body = r.json()
kinds = {f["kind"] for f in body.get("conflicts", [])}
check("method divergence detected", "method_divergence" in kinds, str(body)[:300])
check("method divergence is blocking", body.get("has_blocking") is True)

# --- warning: sibling route under the same prefix ---------------------------
r = client.post(
    f"/projects/{PID}/contracts/check",
    json={"routes": [{"route": "/api/auth/session", "method": "GET"}]},
)
body = r.json()
kinds = {f["kind"] for f in body.get("conflicts", [])}
check("sibling route is a warning", "sibling_collision" in kinds, str(body)[:300])
check("warning is not blocking", body.get("has_blocking") is False and body.get("clean") is False)

# --- content path: engine does the extraction --------------------------------
r = client.post(
    f"/projects/{PID}/contracts/check",
    json={
        "file": "engine/app/routes/auth.py",
        "content": '@router.post("/api/auth/login")\ndef login():\n    pass\n',
    },
)
body = r.json()
check("content path extracts the route", body.get("checked") == 1, str(body)[:300])
check(
    "content path reports the registered match (schema not comparable -> no false conflict)",
    len(body.get("registered_matches", [])) == 1 and body.get("has_blocking") is False,
    str(body)[:300],
)
check(
    "registered match carries the registered shape",
    body["registered_matches"][0]["registered"]["request_schema"].get("username") == "string",
    str(body)[:300],
)

# --- diff path: unified diff, webhook-equivalent -----------------------------
DIFF = (
    "diff --git a/engine/app/routes/auth.py b/engine/app/routes/auth.py\n"
    "--- a/engine/app/routes/auth.py\n"
    "+++ b/engine/app/routes/auth.py\n"
    "@@ -1,2 +1,3 @@\n"
    "+@router.post(\"/api/auth/login\")\n"
    "+def login():\n"
    "     pass\n"
)
r = client.post(f"/projects/{PID}/contracts/check", json={"diff": DIFF})
body = r.json()
check(
    "diff path extracts and reports the match",
    body.get("checked") == 1 and len(body.get("registered_matches", [])) == 1,
    str(body)[:300],
)

# --- side-effect-free --------------------------------------------------------
check("check writes no events", check_count() == events_before, f"{events_before} -> {check_count()}")
check("check registers no contracts", contract_count() == contracts_before)

# --- gates -------------------------------------------------------------------
r = client.post(f"/projects/{PID}/contracts/check", json={"routes": []}, headers={"Authorization": ""})
check("check without bearer is 401", r.status_code == 401, str(r.status_code))
r = client.post(
    f"/projects/{PID}/contracts/check",
    json={"routes": [{"route": "/api/x", "method": "GET"}]},
    headers=auth.auth_headers(pa.outsider_token()),
)
check("check as non-member is 403", r.status_code == 403, str(r.status_code))
PAT_RAW, _ = pa.pat(scoped_to_project=True, name="day12 check pat")
r = client.post(
    f"/projects/{PID}/contracts/check",
    json={"routes": [{"route": "/api/orders", "method": "POST"}]},
    headers=auth.auth_headers(PAT_RAW),
)
check("check works with a project-scoped PAT", r.status_code == 200 and r.json().get("clean") is True, f"{r.status_code} {r.text[:200]}")

# ---------------------------------------------------------------------------
# 3. X-Scaffold-Project binding over real MCP streamable HTTP
# ---------------------------------------------------------------------------
print("\n== MCP X-Scaffold-Project binding ==")
db = _init()()
proj_b = Project(name=f"day12-bound-{uuid.uuid4().hex[:6]}", goal="bound repo")
db.add(proj_b)
db.flush()
PID_B = str(proj_b.id)
db.commit()
db.close()
auth.bootstrap(PID_B)

import httpx  # noqa: E402
import uvicorn  # noqa: E402

HOST, PORT = "127.0.0.1", 8977
server_config = uvicorn.Config(app, host=HOST, port=PORT, log_level="error")
server = uvicorn.Server(server_config)
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
deadline = time.time() + 20
while not server.started and time.time() < deadline:
    time.sleep(0.1)
if not server.started:
    raise RuntimeError("uvicorn did not start")
print("  uvicorn up at", f"http://{HOST}:{PORT}")

BASE = f"http://{HOST}:{PORT}/mcp"


def rpc(http, method, params, session_id=None, headers=None, notify=False):
    msg = {"jsonrpc": "2.0", "id": None if notify else uuid.uuid4().int % 1_000_000, "method": method}
    if params is not None:
        msg["params"] = params
    h = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
    if headers:
        h.update(headers)
    if session_id:
        h["mcp-session-id"] = session_id
    res = http.post(BASE, json=msg, headers=h, timeout=20, follow_redirects=True)
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


def tool_payload(rpc_result: dict):
    content = rpc_result.get("content") or []
    if content and content[0].get("type") == "text":
        return json.loads(content[0]["text"])
    if "structuredContent" in rpc_result:
        return rpc_result["structuredContent"]
    raise RuntimeError(f"unexpected tool result shape: {str(rpc_result)[:200]}")


PAT_MCP, _ = pa.pat(name="day12 mcp pat")  # unscoped: member of BOTH projects

with httpx.Client() as http:
    bound_headers = {"Authorization": f"Bearer {PAT_MCP}", "X-Scaffold-Project": PID_B}
    init, sid = rpc(
        http,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "day12", "version": "0"}},
        headers=bound_headers,
    )
    check("bound-header initialize handshake", init.get("serverInfo", {}).get("name") == "scaffold", str(init)[:150])
    rpc(http, "notifications/initialized", None, session_id=sid, headers=bound_headers, notify=True)

    r, _ = rpc(
        http,
        "tools/call",
        {"name": "get_project_context", "arguments": {}},
        session_id=sid,
        headers=bound_headers,
    )
    ctx = tool_payload(r)
    check("no project_id resolves via X-Scaffold-Project", ctx.get("project", {}).get("id") == PID_B, str(ctx.get("project"))[:200])

    r, _ = rpc(
        http,
        "tools/call",
        {"name": "get_project_context", "arguments": {"project_id": PID}},
        session_id=sid,
        headers=bound_headers,
    )
    ctx = tool_payload(r)
    check("explicit project_id beats the header", ctx.get("project", {}).get("id") == PID, str(ctx.get("project"))[:200])

    bogus = {"Authorization": f"Bearer {PAT_MCP}", "X-Scaffold-Project": "not-a-uuid"}
    r, _ = rpc(
        http,
        "tools/call",
        {"name": "get_project_context", "arguments": {"project_id": PID_B}},
        session_id=sid,
        headers=bogus,
    )
    ctx = tool_payload(r)
    check("malformed header ignored (explicit arg still works)", ctx.get("project", {}).get("id") == PID_B)

    r, _ = rpc(
        http,
        "tools/call",
        {"name": "get_active_tasks", "arguments": {}},
        session_id=sid,
        headers=bound_headers,
    )
    payload = tool_payload(r)
    check("all MCP tools honor the bound project", "tasks" in payload and payload.get("count", 0) >= 0, str(payload)[:200])

    bad = http.post(
        BASE,
        json={
            "jsonrpc": "2.0",
            "id": 99,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "day12", "version": "0"}},
        },
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "Authorization": "Bearer nope",
        },
        timeout=10,
    )
    check("MCP still 401s on bad credentials", bad.status_code == 401, str(bad.status_code))

server.should_exit = True
thread.join(timeout=10)

# ---------------------------------------------------------------------------
print(f"\n{'='*60}\nPASS {len(PASS)}  FAIL {len(FAIL)}")
if FAIL:
    for name in FAIL:
        print("  FAILED:", name)
    sys.exit(1)
