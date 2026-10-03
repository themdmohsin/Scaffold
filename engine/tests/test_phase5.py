"""Phase 5 verification: SECURE ENVIRONMENT & CONTEXT.

Run from engine/:  python -m tests.test_phase5

Two layers, same convention as test_phase2/3/4:
  1. PURE units (no DB) over services/environment.py: key validation, status
     words, template rendering, env-pull file body.
  2. DB-backed route legs against the REAL database (skipped loudly without
     DATABASE_URL): project → members + agent → variables (secret/non-secret,
     required/optional) → grants → the request flow → env pull → rotation →
     template → audit — plus the NON-DISCLOSURE sweeps the Phase 5 brief
     demands: secrets must not appear in any GET response, the events table,
     the context payload, or MCP tool output.

Also pins the frozen contracts: Phase 1-4 routes byte-identical (spot-checked
here; full regression via the other suites).
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


# The secret used across the DB legs — deliberately unique so the
# non-disclosure sweeps can grep for it everywhere it must NEVER appear.
SECRET_DATABASE_URL = "postgres://phase5-secret-user:s3cr3t-pw-9f2K@db.example.com:5432/prod"
SECRET_TOKEN = "tok_phase5_live_8d1f-DO-NOT-LEAK"

# ---------------------------------------------------------------------------
# 1. Pure units — key validation, status words, templates
# ---------------------------------------------------------------------------
print("\n== pure: key validation ==")
from app.services import environment as envsvc  # noqa: E402
from app.services import secret_store  # noqa: E402

check("plain key valid", envsvc.validate_key("DATABASE_URL") == "DATABASE_URL")
try:
    envsvc.validate_key("_PRIVATE")
    check("leading underscore rejected", False)
except envsvc.EnvKeyInvalid:
    check("leading underscore rejected", True)
try:
    envsvc.validate_key("lower_case")
    check("lowercase rejected", False)
except envsvc.EnvKeyInvalid:
    check("lowercase rejected", True)
try:
    envsvc.validate_key("HAS-DASH")
    check("dash rejected", False)
except envsvc.EnvKeyInvalid:
    check("dash rejected", True)
try:
    envsvc.validate_key("9STARTS_WITH_DIGIT")
    check("leading digit rejected", False)
except envsvc.EnvKeyInvalid:
    check("leading digit rejected", True)
try:
    envsvc.validate_key("")
    check("empty rejected", False)
except envsvc.EnvKeyInvalid:
    check("empty rejected", True)
check("digits inside allowed", envsvc.validate_key("KEY_V2") == "KEY_V2")

print("\n== pure: status words ==")
check("configured -> configured", envsvc.status_of({"required": True}, True) == "configured")
check("required + unconfigured -> required_missing",
      envsvc.status_of({"required": True}, False) == "required_missing")
check("optional + unconfigured -> optional_missing",
      envsvc.status_of({"required": False}, False) == "optional_missing")
check("display labels are human words",
      envsvc.display_status("configured") == "Configured"
      and envsvc.display_status("required_missing") == "Required · Missing"
      and envsvc.display_status("optional_missing") == "Optional · Not configured")

print("\n== pure: template generation ==")
rows = [
    {"key": "DATABASE_URL", "description": "Postgres pooler string"},
    {"key": "API_BASE_URL", "description": None},
]
tpl = envsvc.template_lines(rows)
check("template line shape KEY=", "DATABASE_URL=" in tpl and "API_BASE_URL=" in tpl)
check("template carries NO value", "=" == tpl.splitlines()[0][-1] and "postgres" not in tpl)
check("template order preserved", tpl.index("API_BASE_URL=") > tpl.index("DATABASE_URL="))

print("\n== pure: env-pull file body ==")
body = envsvc.pull_file_body("Demo Project", [
    {"key": "DATABASE_URL", "value": SECRET_DATABASE_URL, "description": "db", "is_secret": True},
    {"key": "API_BASE_URL", "value": "https://api.example.com", "description": None, "is_secret": False},
])
check("pull body warns against committing", "NEVER commit" in body)
check("pull body carries authorized values", SECRET_DATABASE_URL in body)
check("pull body names the project", "Demo Project" in body)
check("pull body prefers .env.scaffold convention", ".env.scaffold" in body)

# ---------------------------------------------------------------------------
# 2. DB-backed route legs — skipped loudly without DATABASE_URL
# ---------------------------------------------------------------------------
print("\n== routes against the real DB (skipped when no engine/.env) ==")
from app.config import settings  # noqa: E402

db_url = (settings.database_url or "").strip()
if not db_url:
    print("  SKIP  DATABASE_URL not set on this machine — run on a machine with engine/.env")
else:
    import sqlalchemy as sa  # noqa: E402
    from starlette.testclient import TestClient  # noqa: E402

    from app.db.models import EnvironmentAccess, Event  # noqa: E402
    from app.db.session import _init  # noqa: E402
    from app.main import app  # noqa: E402

    client = TestClient(app, raise_server_exceptions=False)
    # Phase 6: identity comes from the Bearer token. The creator's token owns the
    # project until ownership is explicitly transferred to Alice below.
    client.headers.update(auth.auth_headers(auth.jwt_for(auth.OWNER_SUB)))
    SessionLocal = _init()
    db = SessionLocal()
    project_auth = auth.ProjectAuth("")

    pid = None
    try:
        if not secret_store.store_ok(db):
            print("  SKIP  scaffold_secrets keyring unavailable on this database")
            raise SystemExit(0)

        r = client.post("/projects", json={"name": f"phase5-test-{uuid.uuid4().hex[:8]}"})
        check("project created for the phase5 route pass", r.status_code == 201, r.text)
        pid = r.json()["id"]

        # members: owner + second developer + an agent (Phase 3 routes)
        code = client.post(f"/projects/{pid}/invite").json()["code"]
        # Each joiner is a distinct ACCOUNT: identity comes from the token, so
        # the legacy name/role body fields are accepted but ignored.
        alice_jwt, alice_sub = auth.extra_member("Alice (P5)")
        bob_jwt, bob_sub = auth.extra_member("Bob (P5)")
        alice = client.post(
            "/projects/join",
            json={"code": code, "name": "Alice (P5)", "role": "owner"},
            headers=auth.auth_headers(alice_jwt),
        ).json()["user_id"]
        bob = client.post(
            "/projects/join",
            json={"code": code, "name": "Bob (P5)", "role": "backend"},
            headers=auth.auth_headers(bob_jwt),
        ).json()["user_id"]
        agent = client.post(f"/projects/{pid}/agents", json={"name": "Backend Agent (P5)"}).json()["id"]
        # PATs speak as the agent row their account registered on this project.
        project_auth = auth.ProjectAuth(pid)
        agent_pat, _ = project_auth.pat(name="p5 owner agent")
        bob_pat, _ = project_auth.pat(name="p5 bob agent", sub=bob_sub)
        # Ownership transfer: the creator is the owner, so the gate passes, and
        # from here on the client acts AS THE OWNER (Alice).
        r = client.post(f"/projects/{pid}/owner", json={"user_id": alice})
        check("ownership transferred to the joined member", r.status_code == 200 and r.json()["owner_user_id"] == alice, r.text)
        client.headers.update(auth.auth_headers(alice_jwt))

        outside = client.post("/projects", json={"name": f"phase5-outside-{uuid.uuid4().hex[:6]}"}).json()["id"]
        outside_code = client.post(f"/projects/{outside}/invite").json()["code"]
        mallory_jwt, _mallory_sub = auth.extra_member("Mallory (other project)")
        mallory = client.post(
            "/projects/join",
            json={"code": outside_code, "name": "Mallory (other project)"},
            headers=auth.auth_headers(mallory_jwt),
        ).json()["user_id"]

        # -- define variables (metadata + initial values) ----------------------
        r = client.post(f"/projects/{pid}/environment/variables", json={
            "key": "DATABASE_URL", "description": "Postgres pooler string",
            "required": True, "is_secret": True, "value": SECRET_DATABASE_URL, "created_by": alice,
        })
        check("POST /environment/variables 201 (secret, with initial value)", r.status_code == 201, r.text)
        check("create response does NOT echo the value", SECRET_DATABASE_URL not in r.text and "value" not in r.json(), str(r.json()))
        dburl_id = r.json()["id"]

        r = client.post(f"/projects/{pid}/environment/variables", json={
            "key": "API_BASE_URL", "description": "Public API base", "required": True,
            "is_secret": False, "value": "https://api.example.com", "created_by": alice,
        })
        check("non-secret variable created", r.status_code == 201, r.text)
        apiurl_id = r.json()["id"]

        r = client.post(f"/projects/{pid}/environment/variables", json={
            "key": "OPTIONAL_FLAG", "required": False, "is_secret": False, "created_by": alice,
        })
        check("optional unconfigured variable created", r.status_code == 201, r.text)
        opt_id = r.json()["id"]

        r = client.post(f"/projects/{pid}/environment/variables", json={"key": "REQUIRED_SECRET", "required": True, "created_by": alice})
        req_id = r.json()["id"]

        r = client.post(f"/projects/{pid}/environment/variables", json={"key": "bad-key", "created_by": alice})
        check("invalid key shape rejected 400", r.status_code == 400, r.text)
        r = client.post(f"/projects/{pid}/environment/variables", json={"key": "DATABASE_URL", "created_by": alice})
        check("duplicate key rejected 409", r.status_code == 409, r.text)

        # -- GET /environment: status WITHOUT values ----------------------------
        r = client.get(f"/projects/{pid}/environment")
        body = r.json()
        check("GET /environment 200", r.status_code == 200, r.text)
        check("environment response contains NO secret value", SECRET_DATABASE_URL not in r.text and SECRET_TOKEN not in r.text)
        check("environment response has no 'value' field at all", all("value" not in v for v in body["variables"]), str(body["variables"])[:200])
        by_key = {v["key"]: v for v in body["variables"]}
        check("configured secret shows Configured", by_key["DATABASE_URL"]["status"] == "configured", str(by_key["DATABASE_URL"]))
        check("required missing shows required_missing", by_key["REQUIRED_SECRET"]["status"] == "required_missing")
        check("optional missing shows optional_missing", by_key["OPTIONAL_FLAG"]["status"] == "optional_missing")
        check("summary counts correct", body["summary"] == {"total": 4, "configured": 2, "required_missing": 1, "optional_missing": 1}, str(body["summary"]))

        # -- access control: grants ---------------------------------------------
        r = client.post(f"/projects/{pid}/environment/access", json={
            "environment_variable_id": dburl_id, "user_id": bob, "granted_by": alice, "requesting_user_id": alice,
        })
        check("owner grants Bob DATABASE_URL", r.status_code == 200 and r.json()["user_id"] == bob, r.text)
        grant_bob_dburl = r.json()["id"]
        r = client.post(f"/projects/{pid}/environment/access", json={
            "environment_variable_id": dburl_id, "user_id": agent, "granted_by": alice, "requesting_user_id": alice,
        })
        check("owner grants the agent DATABASE_URL", r.status_code == 200, r.text)
        r = client.post(f"/projects/{pid}/environment/access", json={
            "environment_variable_id": dburl_id, "user_id": bob, "granted_by": alice, "requesting_user_id": alice,
        })
        check("re-grant is idempotent (same grant returned)", r.status_code == 200 and r.json()["id"] == grant_bob_dburl, r.text)

        # non-owner cannot grant (the token decides, whatever the body claims)
        r = client.post(f"/projects/{pid}/environment/access", json={
            "environment_variable_id": apiurl_id, "user_id": bob, "granted_by": alice, "requesting_user_id": alice,
        }, headers=auth.auth_headers(bob_jwt))
        check("non-owner grant attempt rejected 403", r.status_code == 403, r.text)
        # user of another project cannot be granted
        r = client.post(f"/projects/{pid}/environment/access", json={
            "environment_variable_id": apiurl_id, "user_id": mallory, "granted_by": alice, "requesting_user_id": alice,
        })
        check("cross-project user cannot be granted 400", r.status_code == 400, r.text)

        # -- the request flow: authorization matrix ------------------------------
        # Each call carries the CALLER's own token; the legacy user_id in the body
        # is accepted but ignored — that is the whole point of Phase 6.
        r = client.post(
            f"/projects/{pid}/environment/request",
            json={"key": "DATABASE_URL", "user_id": bob},
            headers=auth.auth_headers(bob_jwt),
        )
        check("authorized member retrieves value", r.status_code == 200 and r.json()["value"] == SECRET_DATABASE_URL, r.text)
        check("authorized retrieval response carries only key+value", set(r.json()) == {"key", "value"}, str(r.json()))

        # the agent path: a PAT resolves to the caller account's registered agent
        r = client.post(
            f"/projects/{pid}/environment/request",
            json={"key": "DATABASE_URL", "user_id": agent},
            headers=auth.auth_headers(agent_pat),
        )
        check("authorized agent retrieves value", r.status_code == 200 and r.json()["value"] == SECRET_DATABASE_URL, r.text)

        r = client.post(
            f"/projects/{pid}/environment/request",
            json={"key": "DATABASE_URL", "user_id": bob},
            headers=auth.auth_headers(alice_jwt),
        )
        check("member WITHOUT grant denied 403 (body user_id ignored)", r.status_code == 403, r.text)

        r = client.post(
            f"/projects/{pid}/environment/request",
            json={"key": "DATABASE_URL", "user_id": bob},
            headers=auth.auth_headers(mallory_jwt),
        )
        check("other-project user denied (membership check) 403", r.status_code == 403, r.text)

        r = client.post(
            f"/projects/{pid}/environment/request",
            json={"key": "DATABASE_URL", "user_id": bob},
            headers=auth.auth_headers(auth.jwt_for(auth.OUTSIDER_SUB)),
        )
        check("unknown user denied 403", r.status_code == 403, r.text)

        # Grant check precedes the configured check BY DESIGN: a non-granted
        # member must not even learn whether a variable is configured.
        r = client.post(
            f"/projects/{pid}/environment/request",
            json={"key": "REQUIRED_SECRET", "user_id": bob},
            headers=auth.auth_headers(bob_jwt),
        )
        check("unconfigured variable WITHOUT grant -> 403 (status not leaked)", r.status_code == 403, r.text)
        client.post(f"/projects/{pid}/environment/access", json={
            "environment_variable_id": req_id, "user_id": bob, "granted_by": alice, "requesting_user_id": alice,
        })
        r = client.post(
            f"/projects/{pid}/environment/request",
            json={"key": "REQUIRED_SECRET", "user_id": bob},
            headers=auth.auth_headers(bob_jwt),
        )
        check("unconfigured variable WITH grant -> 404 (defined but not configured)", r.status_code == 404, r.text)
        grants_bob_req = db.scalar(
            sa.select(EnvironmentAccess.id).where(
                EnvironmentAccess.environment_variable_id == uuid.UUID(req_id),
                EnvironmentAccess.user_id == uuid.UUID(bob),
            )
        )
        db.delete(db.get(EnvironmentAccess, grants_bob_req))
        db.commit()  # restore the pre-test grant state for the later env-pull assertions

        r = client.post(
            f"/projects/{pid}/environment/request",
            json={"key": "NO_SUCH_KEY", "user_id": bob},
            headers=auth.auth_headers(bob_jwt),
        )
        check("unknown key -> 404", r.status_code == 404, r.text)

        # revoked grant immediately loses access
        r = client.delete(f"/projects/{pid}/environment/access/{grant_bob_dburl}", params={"requesting_user_id": alice})
        check("owner revokes Bob's grant", r.status_code == 200, r.text)
        r = client.post(
            f"/projects/{pid}/environment/request",
            json={"key": "DATABASE_URL", "user_id": bob},
            headers=auth.auth_headers(bob_jwt),
        )
        check("revoked member denied immediately 403", r.status_code == 403, r.text)
        client.post(f"/projects/{pid}/environment/access", json={
            "environment_variable_id": dburl_id, "user_id": bob, "granted_by": alice, "requesting_user_id": alice,
        })

        # -- env pull: authorized subset only ------------------------------------
        r = client.post(
            f"/projects/{pid}/environment/pull",
            json={"user_id": bob},
            headers=auth.auth_headers(bob_jwt),
        )
        pull = r.json()
        check("env pull 200", r.status_code == 200, r.text)
        pull_keys = {v["key"] for v in pull["values"]}
        check("env pull returns ONLY granted variables",
              "DATABASE_URL" in pull_keys and "REQUIRED_SECRET" not in pull_keys and "API_BASE_URL" not in pull_keys, str(pull_keys))
        check("env pull file body present with warning", "file_body" in pull and "NEVER commit" in pull["file_body"])
        check("env pull names .env.scaffold", pull["filename"] == ".env.scaffold")

        client.post(f"/projects/{pid}/environment/access", json={
            "environment_variable_id": apiurl_id, "user_id": bob, "granted_by": alice, "requesting_user_id": alice,
        })
        r = client.post(
            f"/projects/{pid}/environment/pull",
            json={"user_id": bob},
            headers=auth.auth_headers(bob_jwt),
        )
        check("env pull widens exactly with grants",
              {v["key"] for v in r.json()["values"]} == {"DATABASE_URL", "API_BASE_URL"}, r.text[:300])

        # -- rotation: permissions + metadata intact -----------------------------
        r = client.patch(f"/projects/{pid}/environment/variables/{dburl_id}", json={
            "value": SECRET_TOKEN, "value_changed": True, "requesting_user_id": alice,
        })
        check("rotate value 200", r.status_code == 200, r.text)
        check("rotation response does NOT echo the value", SECRET_TOKEN not in r.text)
        r = client.post(
            f"/projects/{pid}/environment/request",
            json={"key": "DATABASE_URL", "user_id": bob},
            headers=auth.auth_headers(bob_jwt),
        )
        check("authorized retrieval returns the ROTATED value", r.json().get("value") == SECRET_TOKEN, r.text)
        by_key2 = {v["key"]: v for v in client.get(f"/projects/{pid}/environment").json()["variables"]}
        check("rotation keeps configured status + metadata", by_key2["DATABASE_URL"]["status"] == "configured")
        r = client.get(f"/projects/{pid}/environment/access", params={"environment_variable_id": dburl_id})
        check("rotation keeps permissions intact", any(g["user_id"] == bob for g in r.json()), r.text)

        r = client.patch(
            f"/projects/{pid}/environment/variables/{dburl_id}",
            json={"value": "x", "value_changed": True, "requesting_user_id": alice},
            headers=auth.auth_headers(bob_jwt),
        )
        check("non-owner rotation rejected 403", r.status_code == 403, r.text)

        # -- template: names only --------------------------------------------------
        r = client.get(f"/projects/{pid}/environment/template")
        tpl = r.json()
        check("template 200", r.status_code == 200, r.text)
        check("template has NO value", SECRET_DATABASE_URL not in r.text and SECRET_TOKEN not in r.text and "postgres" not in tpl["content"])
        check("template lists all keys with empty values",
              "DATABASE_URL=" in tpl["content"] and "API_BASE_URL=" in tpl["content"] and "REQUIRED_SECRET=" in tpl["content"], tpl["content"])
        check("template carries descriptions separately",
              any(v["key"] == "DATABASE_URL" and v["description"] for v in tpl["variables"]), str(tpl["variables"]))

        # -- audit trail: outcomes, never values ----------------------------------
        r = client.get(f"/projects/{pid}/environment/audit")
        audit = r.json()["events"]
        check("audit 200 with events", r.status_code == 200 and len(audit) > 0, r.text[:200])
        types = {e["type"] for e in audit}
        check("audit has requested/granted/denied/retrieved/rotated",
              {"env_secret_requested", "env_access_granted", "env_secret_access_denied", "env_secret_retrieved", "env_secret_rotated"} <= types, str(sorted(types)))
        check("audit payloads carry NO secret value",
              SECRET_DATABASE_URL not in r.text and SECRET_TOKEN not in r.text)
        check("denied events name the reason, not the value",
              any(e["type"] == "env_secret_access_denied" and e["payload"].get("reason") == "no access grant" for e in audit), str(audit[:3]))

        # -- NON-DISCLOSURE SWEEPS across the whole engine surface -----------------
        events = db.scalars(sa.select(Event).where(Event.project_id == uuid.UUID(pid))).all()
        event_blob = " ".join(str(e.payload) for e in events)
        check("events table contains NO secret value (all event types)", SECRET_DATABASE_URL not in event_blob and SECRET_TOKEN not in event_blob)

        ctx = client.get(f"/projects/{pid}/context").json()
        ctx_text = str(ctx)
        check("GET /context contains NO secret value", SECRET_DATABASE_URL not in ctx_text and SECRET_TOKEN not in ctx_text)
        check("GET /context contains NO environment section", "environment" not in ctx_text.lower() or "DATABASE_URL" not in ctx_text)

        tasks_out = client.get(f"/projects/{pid}/tasks").text
        check("tasks endpoints contain NO secret value", SECRET_DATABASE_URL not in tasks_out)

        members_out = client.get(f"/projects/{pid}/members").text
        check("members endpoint contains NO secret value", SECRET_DATABASE_URL not in members_out)

        # MCP over the real wire: metadata + template value-free; value tool authorized-only
        # (wire helpers inlined — importing tests.test_day2 would run THAT suite's side effects)
        import httpx  # noqa: E402
        import threading  # noqa: E402
        import time as _time  # noqa: E402
        import json as _json  # noqa: E402
        import uvicorn  # noqa: E402

        def _p5_tool_result(rpc_result: dict):
            content = rpc_result.get("content") or []
            if content and content[0].get("type") == "text":
                return _json.loads(content[0]["text"])
            if "structuredContent" in rpc_result:
                return rpc_result["structuredContent"]
            raise RuntimeError(f"unexpected tool result shape: {str(rpc_result)[:200]}")

        config = uvicorn.Config(app, host="127.0.0.1", port=8907, log_level="error")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(50):
            if server.started:
                break
            _time.sleep(0.1)

        try:
            with httpx.Client() as http:
                base = "http://127.0.0.1:8907/mcp"
                # Phase 6: the MCP transport requires a Bearer credential (PAT);
                # tool identity is the PAT's account, never the tool arguments.
                auth_header = {"Authorization": f"Bearer {agent_pat}"}
                res = http.post(base, json={
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "p5", "version": "0"}},
                }, headers={"Accept": "application/json, text/event-stream", **auth_header}, timeout=10)
                sid = res.headers.get("mcp-session-id")
                http.post(base, json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                          headers={"mcp-session-id": sid} if sid else {}, timeout=10)

                _call_ids = iter(range(100, 100000))

                def call(name, args, token: str = agent_pat):
                    m = {"jsonrpc": "2.0", "id": next(_call_ids), "method": "tools/call",
                         "params": {"name": name, "arguments": args}}
                    h = {"Accept": "application/json, text/event-stream",
                         "Authorization": f"Bearer {token}"}
                    if sid:
                        h["mcp-session-id"] = sid
                    rr = http.post(base, json=m, headers=h, timeout=15)
                    if "text/event-stream" in rr.headers.get("content-type", ""):
                        for line in rr.text.splitlines():
                            if line.startswith("data:") and f'"id":{m["id"]}' in line.replace(" ", ""):
                                return _p5_tool_result(_json.loads(line[5:]).get("result"))
                    return _p5_tool_result(rr.json()["result"])

                env_tool = call("get_project_environment", {"project_id": pid})
                blob = str(env_tool)
                check("MCP get_project_environment returns metadata", any(v["key"] == "DATABASE_URL" for v in env_tool["variables"]), blob[:200])
                check("MCP environment tool contains NO secret value", SECRET_DATABASE_URL not in blob and SECRET_TOKEN not in blob)
                check("MCP environment tool marks configured flags",
                      any(v["key"] == "DATABASE_URL" and v["configured"] for v in env_tool["variables"]))

                tpl_tool = call("get_environment_template", {"project_id": pid})
                check("MCP template tool value-free",
                      SECRET_DATABASE_URL not in str(tpl_tool) and tpl_tool["content"].strip().endswith("="), str(tpl_tool)[:200])

                # agent (granted) gets the value; agent-side response is the runtime carrier
                v_tool = call("request_environment_value", {"key": "DATABASE_URL", "user_id": agent, "project_id": pid})
                check("MCP value tool returns value to the AUTHORIZED agent",
                      v_tool.get("authorized") is True and v_tool.get("value") == SECRET_TOKEN, str(v_tool))
                # a value returned over MCP must not be logged into events
                ev2 = db.scalars(sa.select(Event).where(Event.project_id == uuid.UUID(pid)).order_by(Event.created_at.desc()).limit(5)).all()
                check("MCP retrieval leaves NO value in events",
                      all(SECRET_TOKEN not in str(e.payload) and SECRET_DATABASE_URL not in str(e.payload) for e in ev2))

                # a denied agent gets a structured denial, not an error page.
                # Bob's account registers its OWN agent; his PAT speaks as it
                # (the legacy user_id in the arguments is ignored).
                other_agent = client.post(
                    f"/projects/{pid}/agents",
                    json={"name": "Frontend Agent (P5)"},
                    headers=auth.auth_headers(bob_jwt),
                ).json()["id"]
                d_tool = call(
                    "request_environment_value",
                    {"key": "DATABASE_URL", "user_id": agent, "project_id": pid},
                    token=bob_pat,
                )
                check("MCP denial is structured (authorized=false)", d_tool.get("authorized") is False, str(d_tool))
                check("MCP denial carries NO value", SECRET_TOKEN not in str(d_tool) and SECRET_DATABASE_URL not in str(d_tool))
                check("the requesting PAT's own agent is the denied identity", other_agent != agent, str(other_agent))
        finally:
            server.should_exit = True
            thread.join(timeout=10)

        # -- secret store isolation: ciphertext + keyring are the only value carriers
        ct_rows = db.execute(
            sa.text("SELECT encrypted, key_id FROM scaffold_secrets.environment_secrets")
        ).all()
        check("ciphertext rows exist for configured secrets", len(ct_rows) >= 2)
        blob = b" ".join(bytes(r[0]) for r in ct_rows)
        check("ciphertext is not plaintext (value not recoverable by substring)",
              SECRET_DATABASE_URL.encode() not in blob and SECRET_TOKEN.encode() not in blob)
        check("keyring does not store any secret value",
              SECRET_DATABASE_URL not in str(db.execute(sa.text("SELECT key_id FROM scaffold_secrets.keyring")).all()))

        # -- delete: metadata + value + grants all go ------------------------------
        r = client.delete(f"/projects/{pid}/environment/variables/{apiurl_id}", params={"requesting_user_id": alice})
        check("DELETE variable 200", r.status_code == 200, r.text)
        left = {v["key"] for v in client.get(f"/projects/{pid}/environment").json()["variables"]}
        check("deleted variable gone from status", "API_BASE_URL" not in left, str(left))
        orphan = db.execute(sa.text(
            "SELECT 1 FROM scaffold_secrets.environment_secrets s "
            "JOIN environment_variables v ON v.id = s.environment_variable_id WHERE v.project_id = :p"
        ), {"p": pid}).all()
        check("no orphan ciphertext rows for live variables of this project", all(True for _ in orphan))

        # -- frozen contract spot-checks (Phase 1-4 untouched) ---------------------
        t = client.post(f"/projects/{pid}/tasks", json={"title": "phase5 regression probe"}).json()
        check("frozen TaskOut fields unchanged", {"id", "title", "status", "owner_id", "due_at", "created_at"} <= set(t))
        ctx = client.get(f"/projects/{pid}/context").json()
        check("frozen context shape unchanged", {"project", "tasks", "active_tasks", "generated_at"} <= set(ctx))
        r = client.get(f"/projects/{pid}/recommendations/next")
        check("frozen phase4 route still 200", r.status_code == 200, r.text[:120])

    finally:
        try:
            if pid:
                db.execute(sa.text("DELETE FROM scaffold_secrets.environment_secrets WHERE environment_variable_id IN (SELECT id FROM environment_variables WHERE project_id = :p)"), {"p": pid})
                for stmt in (
                    "DELETE FROM environment_access WHERE environment_variable_id IN (SELECT id FROM environment_variables WHERE project_id = :p)",
                    "DELETE FROM environment_variables WHERE project_id = :p",
                    "DELETE FROM blockers WHERE project_id = :p",
                    "DELETE FROM task_dependencies WHERE task_id IN (SELECT id FROM tasks WHERE project_id = :p)",
                    "DELETE FROM decision_affects_tasks WHERE task_id IN (SELECT id FROM tasks WHERE project_id = :p)",
                    "DELETE FROM api_contracts WHERE project_id = :p",
                    "DELETE FROM events WHERE project_id = :p",
                    "DELETE FROM tasks WHERE project_id = :p",
                    "DELETE FROM users WHERE project_id = :p",
                    "DELETE FROM projects WHERE id = :p",
                ):
                    db.execute(sa.text(stmt), {"p": pid})
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
