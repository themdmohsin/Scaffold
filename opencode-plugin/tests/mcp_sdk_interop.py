"""Day 4 interop check: the plugin's hand-rolled MCP client vs the REAL `mcp` SDK server.

Why this exists: `tests/verify_plugin.ts` proves the plugin's hook logic, but it drives a fake
engine written by the same hand that wrote the client — so it cannot catch a wire mismatch.
`engine/tests/test_day2.py` proves the engine's *server* speaks this protocol to a *Python*
client. This closes the loop: a real `mcp`-SDK server (the fixture in `engine_fixture.py`, built
with the engine's own mount code) with fixture tools — no Postgres, no engine app, no LLM.

Run it from anywhere (it re-executes itself with engine/.venv's interpreter if needed):

    python opencode-plugin/tests/mcp_sdk_interop.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine_fixture import FIXTURES, FixtureEngine, check, report, run_driver  # noqa: E402

DRIVER = Path(__file__).resolve().with_name("mcp_sdk_interop.ts")

with FixtureEngine() as engine:
    print(f"\n== real mcp SDK server on {engine.url}/mcp ==")
    driver, proc = run_driver(DRIVER, engine.url)

    if driver is None:
        print(proc.stdout)
        print(proc.stderr)
        print("FAIL  the plugin driver produced no result (see output above)")
        sys.exit(1)

    for entry in driver["checks"]:
        check(entry["name"], entry["ok"], entry.get("detail", ""))
    check("plugin driver exited cleanly", proc.returncode == 0, f"exit {proc.returncode}")

    called = [call["tool"] for call in engine.calls]
    check(
        "the real SDK server received all three frozen tool names",
        set(called) == {"get_project_context", "get_api_contract", "report_change"},
        str(called),
    )
    check(
        "context is refetched exactly once per user message (cache invalidation over the real protocol)",
        called.count("get_project_context") == 2 and engine.calls[0]["tool"] == "get_project_context",
        str(called),
    )
    check(
        "the plugin sent no project_id (engine-side SCAFFOLD_DEFAULT_PROJECT_ID convention)",
        all(call["project_id"] is None for call in engine.calls),
        str(engine.calls),
    )
    contract_call = next((call for call in engine.calls if call["tool"] == "get_api_contract"), {})
    check(
        "contract lookup used the route from the write arguments",
        contract_call.get("route") == FIXTURES["route"],
        str(contract_call),
    )
    check("exactly one change was reported", len(engine.reports) == 1, str(len(engine.reports)))

    sent = engine.reports[0] if engine.reports else {}
    check(
        "reported summary is the deterministic diff metadata",
        sent.get("diff_summary") == "edited engine/app/routes/interop.py (+7 -2 lines)",
        str(sent),
    )
    check("reported files are worktree-relative", sent.get("files_changed") == ["engine/app/routes/interop.py"], str(sent))
    check(
        "no source text crossed the wire (repo rule #4)",
        FIXTURES["sourceMarker"] not in json.dumps({"calls": engine.calls, "reports": engine.reports}),
    )

    if driver["checks"] and not all(c["ok"] for c in driver["checks"]):
        print(proc.stdout)
        print(proc.stderr)

sys.exit(report())
