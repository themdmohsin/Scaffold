"""A real `mcp`-SDK server with fixture tools — the wire-faithful stand-in for the engine.

Shared by `mcp_sdk_interop.py` (does the plugin's client speak the same protocol the engine
serves?) and `acceptance_live.py --self-test` (does the acceptance driver itself work?).

Uses the engine's own mount code (`streamable_http_app()` + `_McpPathFix`, imported from
`engine/app/main.py`, not copied) so a change to how the engine mounts `/mcp` shows up here.
No Postgres, no LLM, no engine app.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "engine"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{extra}]" if extra and not cond else ""))


def report(total_label: str = "RESULT") -> int:
    print(f"\n== {total_label}: {len(PASS)} passed, {len(FAIL)} failed ==")
    if FAIL:
        print("FAILED:", FAIL)
        return 1
    return 0


def _venv_python() -> Path | None:
    for candidate in (ENGINE / ".venv" / "Scripts" / "python.exe", ENGINE / ".venv" / "bin" / "python"):
        if candidate.exists():
            return candidate
    return None


def ensure_mcp() -> None:
    """Re-run the calling script with engine/.venv's interpreter if `mcp` is missing."""
    try:
        import mcp  # noqa: F401

        return
    except ImportError:
        pass
    python = _venv_python()
    entry = Path(sys.argv[0]).resolve()
    if python and Path(sys.executable).resolve() != python.resolve() and entry.exists():
        print(f"re-running with {python}")
        raise SystemExit(subprocess.run([str(python), str(entry), *sys.argv[1:]]).returncode)
    print("FAIL  the `mcp` package is not importable and engine/.venv was not found")
    raise SystemExit(1)


FIXTURES = {
    "projectName": "Interop Fixture Project",
    "decisionText": "Auth uses POST /api/interop/route returning { token }",
    "route": "/api/interop/route",
    "method": "POST",
    "schemaMarker": "interop_email_field",
    "sourceMarker": "INTEROP_SECRET_SOURCE_BODY_c41d",
}

FIXTURE_CONTEXT = {
    "project": {
        "id": "fdcb7511-434b-40c1-a391-0cd45e2150a5",
        "name": FIXTURES["projectName"],
        "goal": "interop",
        "deadline": None,
    },
    "tasks": {"todo": 1, "in_progress": 1, "done": 0},
    "active_tasks": [
        {"id": "t1", "title": "Wire the interop route", "status": "in_progress", "owner_id": None, "due_at": None}
    ],
    "recent_decisions": [
        {"id": "d1", "text": FIXTURES["decisionText"], "created_at": "2026-09-25T09:00:00+00:00"}
    ],
    "relevant_contracts": [{"route": FIXTURES["route"], "method": FIXTURES["method"]}],
    "generated_at": "2026-09-25T09:00:00+00:00",
}

FIXTURE_CONTRACT = {
    "found": True,
    "id": "c1",
    "route": FIXTURES["route"],
    "method": FIXTURES["method"],
    "request_schema": {FIXTURES["schemaMarker"]: "string"},
    "response_schema": {"token": "string"},
    "created_at": "2026-09-25T09:00:00+00:00",
}


class FixtureEngine:
    """Starts the fixture server on a free port; records what clients actually sent."""

    def __init__(self) -> None:
        ensure_mcp()

        from fastapi import FastAPI
        from mcp.server.mcpserver import MCPServer
        from starlette.routing import Route

        sys.path.insert(0, str(ENGINE))
        from app.main import _McpPathFix  # the engine's real mount trick

        self.calls: list[dict] = []
        self.reports: list[dict] = []
        self._server = None
        mcp = MCPServer("scaffold")
        calls, reports = self.calls, self.reports

        @mcp.tool()
        def get_project_context(project_id: str | None = None) -> dict:
            """Fixture stand-in for the frozen tool of the same name."""
            calls.append({"tool": "get_project_context", "project_id": project_id})
            return FIXTURE_CONTEXT

        @mcp.tool()
        def get_api_contract(route: str, project_id: str | None = None) -> dict:
            """Fixture stand-in for the frozen tool of the same name."""
            calls.append({"tool": "get_api_contract", "route": route, "project_id": project_id})
            return dict(FIXTURE_CONTRACT) if route == FIXTURES["route"] else {"found": False, "route": route}

        @mcp.tool()
        def report_change(diff_summary: str, files_changed: list[str], project_id: str | None = None) -> dict:
            """Fixture stand-in for the frozen tool of the same name."""
            calls.append({"tool": "report_change", "project_id": project_id})
            reports.append({"diff_summary": diff_summary, "files_changed": files_changed})
            return {"ok": True, "event_id": "evt-interop-1"}

        wrapped = _McpPathFix(mcp.streamable_http_app())

        @asynccontextmanager
        async def lifespan(_: FastAPI):
            async with mcp.session_manager.run():
                yield

        app = FastAPI(title="Scaffold MCP interop fixture", lifespan=lifespan)
        app.router.routes.append(Route("/mcp", wrapped, methods=["GET", "POST", "DELETE"]))
        app.mount("/mcp", wrapped)

        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        self.port = probe.getsockname()[1]
        probe.close()

        import uvicorn

        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error")
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "FixtureEngine":
        thread = threading.Thread(target=self._server.run, daemon=True)
        thread.start()
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("fixture server did not start")
        self._thread = thread
        return self

    def __exit__(self, *_) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


def run_driver(driver: Path, url: str, fixtures: dict | None = None, env_extra: dict | None = None, timeout: int = 180):
    """Run a TS driver with node; return (parsed result, completed process)."""
    env = {
        **os.environ,
        "SCAFFOLD_ENGINE_URL": url,
        "INTEROP_FIXTURES": json.dumps(fixtures or FIXTURES),
        **(env_extra or {}),
    }
    proc = subprocess.run(["node", str(driver)], capture_output=True, text=True, env=env, timeout=timeout)
    line = next((l for l in reversed(proc.stdout.splitlines()) if l.startswith("__RESULT__")), None)
    return (json.loads(line[len("__RESULT__") :]) if line else None), proc
