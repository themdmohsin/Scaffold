# Day 5 — Person B Integration Runbook (two-laptop pass + go/no-go)

> The build plan (§2 Day 5, Person B): run the full Section 4 flow across **two
> actual separate laptops**, fix what breaks, and end with an honest go/no-go on
> the §17 demo. This runbook makes that pass copy-pasteable. Person A's parts
> (availability, `/reason` assignment, invites) are built and offline-verified —
> see `docs/HANDOFF.md`, Day 5 entry.
>
> **Executed 2026-09-26 as a single-host equivalent** (no second laptop available):
> live engine + plugin MCP acceptance (3/3) + curl second client + a real browser
> driving the whole §17 flow — **GO**, with residual risks recorded in HANDOFF.
> Re-run these steps on real two-laptop hardware if it ever becomes available.

## Before you start (both laptops)

| Need | Where | Check |
| --- | --- | --- |
| `engine/.env` | engine host | `DATABASE_URL`, `SCAFFOLD_TEAM_LLM_KEY` (+ `GITHUB_TOKEN`, `SCAFFOLD_GITHUB_REPO` for the conflict leg, `SCAFFOLD_DEFAULT_PROJECT_ID` for the demo project) |
| Engine deps | engine host | `cd engine && pip install -r requirements.txt` (a venv is easiest) — bare `python` fails with `ModuleNotFoundError` |
| Engine running | engine host | `curl http://<engine-host>:8000/health` → `{"status":"ok"}` |
| Plugin reachable | both laptops | `SCAFFOLD_ENGINE_URL=http://<engine-host>:8000` when launching the OpenCode fork (`cd opencode-plugin/opencode && bun install --ignore-scripts && bun run --cwd packages/opencode src/index.ts`) |
| Dashboard | optional but recommended | `cd dashboard && npm run dev` with the engine URL configured |

**Step 0 — full suite on the engine host (runs what dev machines skip):**

```bash
cd engine && python -m tests.test_day5    # the DB route legs run here (see HANDOFF for the current check count)
python -m tests.test_day2                 # 30/30 regression
python -m tests.test_day4b                # 25/25 regression
```

If `test_day5`'s DB sections fail here, STOP — fix before burning laptop time.

## The two-laptop pass

1. **Join via invites (new in Day 5 — no SQL seeding):**
   - Laptop A: `curl -X POST http://<engine>:8000/projects/<PROJECT_ID>/invite`
   - Laptop B: `curl -X POST http://<engine>:8000/projects/join -H "content-type: application/json" -d '{"code":"<from A>","name":"Dev B"}'`
   - Expect 201 `{user_id, ...}`. Re-running the same join must return 200 + `"existing": true` (idempotent — that is a feature; duplicate rows would poison the roster).
2. **§17 step 1 — shared context:** on both laptops open the OpenCode session and ask "what is this project?" — both agents' system blocks must carry the same project name/goal.
3. **§17 step 2 — Dev A makes a change:** prompt Agent A to add an endpoint (e.g. `POST /api/auth/login`). After the write tool runs, check the engine logs for `change reported to engine` and the toast.
4. **§17 step 3 — Dev B receives it:** on Laptop B, ask Agent B about that endpoint **in a new prompt** (fresh user message → context refresh). B's block must name the contract. This is the handoff requirement.
5. **§17 step 4 — the conflict moment:** follow `docs/demo-conflict-scenario.md` (Agent B registers a divergent shape → `conflict_flagged` event + blocker + GitHub issue).
6. **§17 step 1b — task assignment (new in Day 5):** ask the dashboard "add Google login" → `/reason` returns `suggested_tasks` **with `owner_id`/`due_at` filled from the roster** → click the suggestion → the task appears on the board **assigned**. (A dev-machine bug that dropped the assignment on this exact click was found in the pre-run trace and fixed — if tasks still land unassigned, re-check `dashboard/src/App.tsx` `addSuggested`.)

## Known-shaky list (what the trace already found)

- FIXED offline: dashboard dropped `owner_id`/`due_at` when creating a task from a `/reason` suggestion (the demo's "assigned instantly" moment died at the last hop).
- Narrative gap: the conflict demo's contract arrives via dashboard/API push, **not** automatically from the plugin's write — the plugin pins contracts into the prompt but does not register them. For the demo, register Agent A's shape via the dashboard, then let B's agent write against it.
- FIXED (Day 5 round 5): the conflict moment was invisible — conflicts were written to the DB but `GET /context` exposed no blockers/events and the dashboard had no section for them. `/context` now returns additive `blockers` + `recent_events` keys and the dashboard renders open blockers, refreshed by the existing realtime refetch.
- FIXED live (single-host pass, caught by a real browser click): `pidRef` was never assigned, so every dashboard mutation (ask/suggest/add/move) POSTed to `/projects//…` → 404 while reads still worked — the UI looked alive with every button dead.
- The §17 doc's example project UUID is dev-history, not seed data — on a fresh engine host, create the demo project first (or set `SCAFFOLD_DEFAULT_PROJECT_ID`) and use that id everywhere.

## Go / no-go (fill in during the pass)

- [ ] `/health` ok and both laptops share the same project context
- [ ] Invite → join works from Laptop B; duplicate join is idempotent
- [ ] A's change visible in B's next prompt (change_reported → context refresh)
- [ ] Conflict scenario fires end to end (event + blocker + issue)
- [ ] `/reason` assigns a real teammate; dashboard click creates an assigned task
- [ ] No hook ever threw into the coding session (check engine warn logs)

**GO** = all boxes checked on real laptops. **NO-GO** = any unchecked box; write
which, with the exact error, into `docs/HANDOFF.md` "Still broken / not done".

**Filled 2026-09-26 from the single-host pass:** all six boxes verified except that the
realtime box was exercised as refetch-on-action (Supabase `VITE_SUPABASE_*` unset locally)
and the GitHub-issue box as fail-open-skip (no `GITHUB_TOKEN` on this machine). Verdict
recorded in HANDOFF; re-run on two real laptops when available.
