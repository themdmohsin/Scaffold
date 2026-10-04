"""Day 4 acceptance check: run the plugin's hooks against a LIVE engine and print the evidence.

This is the leg the automated harnesses cannot cover — a real engine, real Postgres, real project
state. It needs no LLM key and no coding session: it drives the same hooks OpenCode calls and
prints the exact block the agent would receive, which is the artifact to paste into HANDOFF.md.

    python opencode-plugin/tests/acceptance_live.py --self-test   # fixture engine (proves the runner)
    python opencode-plugin/tests/acceptance_live.py               # $SCAFFOLD_ENGINE_URL or localhost:8000
    python opencode-plugin/tests/acceptance_live.py --url http://192.168.1.20:8000

Requires a live engine whose .env has DATABASE_URL + SCAFFOLD_DEFAULT_PROJECT_ID
(engine/README steps: pip install -r requirements.txt, apply schema, seed, uvicorn app.main:app).

The live engine authenticates every MCP call with a personal access token
(Authorization: Bearer). Mint one with POST /auth/tokens and export it first:

    SCAFFOLD_TOKEN=scaffold_... python opencode-plugin/tests/acceptance_live.py

    SCAFFOLD_ACCEPTANCE_REPORT=1 python opencode-plugin/tests/acceptance_live.py
also exercises report_change — that WRITES a `change_reported` event to the real project.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine_fixture import FAIL, FixtureEngine, check, report, run_driver  # noqa: E402

DRIVER = Path(__file__).resolve().with_name("acceptance_live.ts")


def run(url: str, label: str, *, crash_after_result: bool = False) -> None:
    print(f"\n== {label}: {url} ==")
    env_extra = {"SCAFFOLD_ACCEPTANCE_CRASH_AFTER_RESULT": "1"} if crash_after_result else None
    driver, proc = run_driver(DRIVER, url, env_extra=env_extra)

    # The block is the artifact a human needs to see, injected or not (plus why, if not).
    print(proc.stdout.rstrip())
    if proc.stderr.strip():
        print(proc.stderr.rstrip())

    # A crashed driver must fail the run even if it managed to print a __RESULT__ line
    # before dying (Node can crash-fail during teardown on Windows).
    check(f"{label}: driver exited cleanly", proc.returncode == 0, f"exit {proc.returncode}")
    if driver is None:
        check(f"{label}: driver produced a result", False, f"driver crashed, exit {proc.returncode}")
        return

    check(f"{label}: engine answered and context was injected", driver["blockChars"] > 0, "; ".join(driver["warnings"]))
    check(f"{label}: injected block stayed bounded", driver["blockChars"] <= 2400, f"{driver['blockChars']} chars")
    check(f"{label}: no engine warnings while reachable", not driver["warnings"], "; ".join(driver["warnings"]))
    if driver["reportsAttempted"]:
        check(f"{label}: report_change round trip accepted", driver["reportsToasted"])


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--self-test", action="store_true", help="run against the fixture engine instead of a live one")
parser.add_argument("--url", default=os.environ.get("SCAFFOLD_ENGINE_URL", "http://localhost:8000"))
args = parser.parse_args()

if args.self_test:
    # Same code path as the live run, pointed at the fixture: proves the runner itself works.
    with FixtureEngine() as engine:
        run(engine.url, "self-test (fixture engine)")
        check("self-test: the fixture actually injected a block", len(engine.calls) > 0, str(engine.calls))

    # The runner must REJECT a driver that dies — even one that printed a result first, which
    # is exactly how Node crash-fails during teardown on Windows. The FAIL lines below are the
    # point; they are pulled back out of the tally so this leg passing does not fail the command.
    print("\n== self-test: a driver that crashes must be reported as a failure ==")
    before = len(FAIL)
    run("http://127.0.0.1:1", "self-test (driver dies after printing a result)", crash_after_result=True)
    detected = FAIL[before:]
    del FAIL[before:]
    check(
        "self-test: the runner rejected the crashed driver",
        bool(detected),
        "the runner accepted a driver that died (no failure was recorded for exit != 0)",
    )
else:
    if not os.environ.get("SCAFFOLD_TOKEN"):
        print(
            "\nThe live engine requires a personal access token on every MCP call.\n"
            "Mint one with POST /auth/tokens (response shows the raw token once), then:\n"
            "    SCAFFOLD_TOKEN=scaffold_... python opencode-plugin/tests/acceptance_live.py\n"
        )
        check("live run supplies SCAFFOLD_TOKEN", False, "missing PAT")
        sys.exit(report())
    run(args.url, "live engine")
    print(
        "\nEvidence for docs/HANDOFF.md: paste the injected block above.\n"
        "For the Day 4 handoff requirement, run this on BOTH machines after Dev A commits —\n"
        "Dev B's block should already carry Dev A's change (decisions/contracts), with nobody restarting anything."
    )

sys.exit(report())
