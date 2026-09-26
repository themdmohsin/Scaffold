"""Live /reason smoke test: real DB + real LLM, everything else deterministic.

The one leg the offline suites cannot cover: does the actual Gemini call return
suggested_tasks whose owner_ids survive deterministic re-validation against the
roster? Run from engine/ (needs engine/.env with DATABASE_URL + the real
SCAFFOLD_TEAM_LLM_KEY):

    python -m app.scripts.live_reason_check

Creates a throwaway project, invites + joins two teammates, asks for work
breakdown on "add google login", prints the verdict, then deletes every row it
created. Exit 0 = pass, 1 = fail.
"""

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings  # noqa: E402


def main() -> int:
    if not (settings.scaffold_team_llm_key or "").strip():
        print("FAIL  SCAFFOLD_TEAM_LLM_KEY is not set in engine/.env — nothing to test")
        return 1
    if not (settings.database_url or "").strip():
        print("FAIL  DATABASE_URL is not set in engine/.env — nothing to test")
        return 1

    import sqlalchemy as sa
    from starlette.testclient import TestClient

    from app.db.session import _init
    from app.main import app
    from app.services import reasoning

    print(f"model: {reasoning.chat_model()}  key: {settings.scaffold_team_llm_key[:6]}...")
    client = TestClient(app, raise_server_exceptions=False)
    SessionLocal = _init()

    deadline = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    name = f"day5-live-{uuid.uuid4().hex[:8]}"

    pid: uuid.UUID | None = None
    try:
        r = client.post("/projects", json={"name": name, "goal": "live LLM check", "deadline": deadline})
        if r.status_code != 201:
            print(f"FAIL  POST /projects -> {r.status_code} {r.text[:200]}")
            return 1
        pid = uuid.UUID(r.json()["id"])
        print(f"project: {pid}")

        r = client.post(f"/projects/{pid}/invite", json={})
        if r.status_code != 201 or "code" not in r.json():
            print(f"FAIL  POST /invite -> {r.status_code} {r.text[:200]}")
            return 1
        code = r.json()["code"]

        joined: list[str] = []
        for teammate in ("Dev A", "Dev B"):
            r = client.post("/projects/join", json={"code": code, "name": teammate})
            if r.status_code != 201 or "user_id" not in r.json():
                print(f"FAIL  POST /join ({teammate}) -> {r.status_code} {r.text[:200]}")
                return 1
            joined.append(r.json()["user_id"])
        print(f"joined: {joined}")

        # Google free-tier capacity flaps (intermittent 503 high demand): retry
        # transient provider failures with backoff; real 4xx failures never retry.
        import time

        body: dict | None = None
        for attempt in range(1, 5):
            r = client.post(f"/projects/{pid}/reason", json={"prompt": "add google login"})
            if r.status_code == 200:
                body = r.json()
                break
            transient = r.status_code in (502, 503) and attempt < 4
            print(f"FAIL  POST /reason -> {r.status_code} {r.text[:200]}" + (f"  (attempt {attempt}/4, retrying)" if transient else ""))
            if not transient:
                return 1
            time.sleep(15 * attempt)
        if body is None:
            return 1
        tasks = body.get("suggested_tasks") or []
        owners = [t.get("owner_id") for t in tasks]
        roster_ok = all(o in joined for o in owners)
        print(f"answer: {body.get('answer', '')[:160]}")
        print(f"suggested_tasks: {len(tasks)}  owners: {owners}")
        if body.get("assignment_notes"):
            print(f"assignment_notes: {body['assignment_notes']}")

        problems: list[str] = []
        if not isinstance(body.get("answer"), str) or not body["answer"].strip():
            problems.append("answer missing/empty")
        if not tasks:
            problems.append("no suggested_tasks returned")
        if not roster_ok:
            problems.append("owner_id outside the real roster — validation did not fire")
        if any(not t.get("title") for t in tasks):
            problems.append("suggested task with empty title")
        if problems:
            print("FAIL  " + "; ".join(problems))
            return 1
        print("LIVE-REASON: PASS — real LLM suggestions carry roster-valid owner_ids")
        return 0
    finally:
        if pid is not None:
            db = SessionLocal()
            try:
                db.execute(sa.text("DELETE FROM events WHERE project_id = :p"), {"p": pid})
                db.execute(sa.text("DELETE FROM tasks WHERE project_id = :p"), {"p": pid})
                db.execute(sa.text("DELETE FROM users WHERE project_id = :p"), {"p": pid})
                db.execute(sa.text("DELETE FROM projects WHERE id = :p"), {"p": pid})
                db.commit()
                print(f"cleaned up {name}")
            except Exception:  # noqa: BLE001 — cleanup must never mask the verdict
                db.rollback()
                print("WARNING: teardown failed — rows may remain for that project id")
            finally:
                db.close()


if __name__ == "__main__":
    sys.exit(main())
