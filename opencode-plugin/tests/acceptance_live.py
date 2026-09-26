"""Day 4 acceptance check: run the plugin's hooks against a LIVE engine and print the evidence.

This is the leg the automated harnesses cannot cover — a real engine, real Postgres, real project
state. It needs no LLM key and no coding session: it drives the same hooks OpenCode calls and
prints the exact block the agent would receive, which is the artifact to paste into HANDOFF.md.

    python opencode-plugin/tests/acceptance_live.py --self-test   # fixture engine (proves the runner)
    python opencode-plugin/tests/acceptance_live.py               # $SCAFFOLD_ENGINE_URL or localhost:8000
    python opencode-plugin/tests/acceptance_live.py --url http://192.168.1.20:8000

Requires a live engine whose .env has DATABASE_URL + SCAFFOLD_DEFAULT_PROJECT_ID
(engine/README steps: pip install -r requirements.txt, apply schema, seed, uvicorn app.main:app).

    SCAFFOLD_ACCEPTANCE_REPORT=1 python opencode-plugin/tests/acceptance_live.py
also exercises report_change — that WRITES a `change_reported` event to the real project.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine_fixture import FixtureEngine, check, report, run_driver  # noqa: E402

DRIVER = Path(__file__).resolve().with_name("acceptance_live.ts")


def run(url: str, label: str) -> None:
    print(f"\n== {label}: {url} ==")
    driver, proc = run_driver(DRIVER, url)

    # The block is the artifact a human needs to see, injected or not (plus why, if not).
    print(proc.stdout.rstrip())
    if proc.stderr.strip():
        print(proc.stderr.rstrip())

    if driver is None:
        check(f"{label}: driver produced a result", False, f"exit {proc.returncode}")
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
else:
    run(args.url, "live engine")
    print(
        "\nEvidence for docs/HANDOFF.md: paste the injected block above.\n"
        "For the Day 4 handoff requirement, run this on BOTH machines after Dev A commits —\n"
        "Dev B's block should already carry Dev A's change (decisions/contracts), with nobody restarting anything."
    )

sys.exit(report())
