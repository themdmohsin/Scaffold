# SCAFFOLD — 6-Day Build Plan (2 people/day)

*This is the execution plan. It assumes only 2 people work on any given day, and different days may have different pairs. The way this stays conflict-free across people and days is **Day 1 freezes every contract** (schema, API shapes, env var names, naming conventions) — everyone after Day 1 builds strictly against what's frozen, never invents their own naming. Read Section 0 fully before assigning anyone, even if you're not coding Day 1 yourself.*

Refer to `scaffold-technical-research.md` (the master architecture doc) for *why* — this doc is the *what, exactly, in what order*.

---

## 0. Ground Rules (apply every day, no exceptions)

**Repo structure — everyone works inside this, nobody invents new top-level folders:**

```
scaffold/
├── engine/                 # FastAPI backend — the "engine" from the architecture doc
│   ├── app/
│   │   ├── main.py
│   │   ├── db/
│   │   │   ├── schema.sql
│   │   │   └── models.py
│   │   ├── routes/
│   │   │   ├── tasks.py
│   │   │   ├── decisions.py
│   │   │   ├── context.py
│   │   │   └── github_webhook.py
│   │   ├── services/
│   │   │   ├── retrieval.py      # Section 7 context engineering
│   │   │   ├── reasoning.py      # LiteLLM calls live here, ONLY here
│   │   │   └── diff_parser.py    # regex extraction from Git diffs
│   │   └── mcp_server.py
│   ├── requirements.txt
│   └── .env.example
├── dashboard/               # React + Vite — team-facing UI
│   ├── src/
│   ├── package.json
│   └── .env.example
├── opencode-plugin/         # the fork + hook plugin
│   └── (structure depends on OpenCode's own plugin API — Day 1 person 2 documents this here once explored)
└── docs/
    ├── API_CONTRACTS.md     # frozen Day 1, append-only after
    ├── SCHEMA.md            # frozen Day 1, append-only after
    └── HANDOFF.md           # every pair updates this at end of their day
```

**Git workflow:**

- One shared repo, `main` is always demo-able (never leave it broken overnight)
- Every person works on their own branch: `day<N>-<initials>-<short-feature>` (e.g. `day2-mk-webhook`)
- Small, frequent commits with plain messages (`add task schema`, `wire realtime to dashboard`)
- PR into `main` at the end of each session, even solo — don't let branches live more than a day
- **Pull `main` before starting any session** — this is the actual conflict-prevention mechanism, not luck

**`docs/HANDOFF.md` — every single day, whoever worked writes 3-5 lines at the end:**

```
## Day N — <date> — <names>
Built: <what's actually working now>
Env vars added: <any new .env keys, with example values>
Still broken / not done: <honest state>
Next pair should start with: <one sentence>
```

*This file is the reason a pair who wasn't there the day before can sit down and know exactly where things stand. Nobody skips writing it, even if the day went badly.*

---

## 1. Frozen Contracts (established Day 1, section 2 — read this before writing any later-day code)

### Database schema (Postgres, via Supabase) — exact table/column names, do not rename later without updating this doc and telling everyone

```sql
-- docs/SCHEMA.md mirrors this exactly

CREATE TABLE projects (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  goal TEXT,
  deadline TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  name TEXT NOT NULL,
  role TEXT
);

CREATE TABLE tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  title TEXT NOT NULL,
  status TEXT DEFAULT 'todo',       -- 'todo' | 'in_progress' | 'done'
  owner_id UUID REFERENCES users(id),
  due_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE task_dependencies (
  task_id UUID REFERENCES tasks(id),
  depends_on_task_id UUID REFERENCES tasks(id),
  PRIMARY KEY (task_id, depends_on_task_id)
);

CREATE TABLE decisions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  text TEXT NOT NULL,
  reasoning TEXT,
  made_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE decision_affects_tasks (
  decision_id UUID REFERENCES decisions(id),
  task_id UUID REFERENCES tasks(id),
  PRIMARY KEY (decision_id, task_id)
);

CREATE TABLE api_contracts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  route TEXT NOT NULL,              -- e.g. '/api/auth/login'
  method TEXT NOT NULL,             -- 'GET' | 'POST' | etc
  request_schema JSONB,
  response_schema JSONB,
  created_by_task_id UUID REFERENCES tasks(id),
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE commits (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  sha TEXT NOT NULL,
  message TEXT,
  author TEXT,
  files_changed TEXT[],
  summary TEXT,                     -- LLM-generated one-liner, Section 6 of master doc
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE blockers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  task_id UUID REFERENCES tasks(id),
  description TEXT,
  resolved BOOLEAN DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID REFERENCES projects(id),
  type TEXT NOT NULL,               -- 'task_created' | 'decision_logged' | 'commit_ingested' | 'conflict_flagged' | ...
  payload JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

Embeddings for pgvector: add an `embedding VECTOR(1536)` column to `decisions` and `api_contracts` once you've picked an embedding model — don't block Day 1 on this, add it Day 3 when retrieval is actually being built.

### API contracts (FastAPI backend, `engine/`) — exact routes, do not rename

```
GET  /projects/:id/context          → returns the "always-on summary" (Section 7 of master doc)
GET  /projects/:id/tasks            → list tasks
POST /projects/:id/tasks            → create a task { title, owner_id?, due_at? }
PATCH /projects/:id/tasks/:task_id  → update status/owner
GET  /projects/:id/decisions        → list decisions
POST /projects/:id/decisions        → log a decision { text, reasoning?, made_by }
GET  /projects/:id/contracts        → list API contracts
POST /projects/:id/contracts        → register a new API contract (called by the plugin hook, Section 4 step 7 of master doc)
POST /projects/:id/github-webhook   → GitHub webhook receiver (push/PR events)
POST /projects/:id/reason           → the umbrella reasoning endpoint { prompt } → { answer, suggested_tasks? } — the ONLY route that calls LiteLLM for a user-facing answer
```

### MCP tool names (exposed by `engine/app/mcp_server.py`) — exact names, the OpenCode plugin calls these verbatim

```
get_project_context()
get_api_contract(route: str)
get_active_tasks()
get_recent_decisions()
report_change(diff_summary: str, files_changed: list[str])
create_task(title: str, owner_id: str | None, due_at: str | None)
```

### Environment variables (`.env.example` in both `engine/` and `dashboard/`) — exact names

```
# engine/.env.example
SUPABASE_URL=
SUPABASE_SERVICE_KEY=
SCAFFOLD_TEAM_LLM_KEY=          # the team's own key, LiteLLM uses this — Section 10 of master doc
GITHUB_WEBHOOK_SECRET=
GITHUB_TOKEN=                   # PAT fallback if GitHub App setup stalls

# dashboard/.env.example
VITE_SUPABASE_URL=
VITE_SUPABASE_ANON_KEY=
VITE_ENGINE_URL=
```

**Nobody adds a new env var without adding it to `.env.example` and a line in `HANDOFF.md` the same day.**

---

## 2. Day-by-Day (2 people each day)

### Day 1 — Foundation & Contracts (the most important day — everything above gets built and frozen today)

**Person 1 — Backend/DB:**

- Create the Supabase project, run `schema.sql` (Section 1 above) exactly as written
- Scaffold FastAPI app (`engine/app/main.py`), get a healthcheck route live, deploy empty skeleton to Railway/Render so the URL exists from day one
- Write `docs/SCHEMA.md` and `docs/API_CONTRACTS.md` matching Section 1 exactly — these become the frozen contract everyone else reads instead of asking questions in the group chat

**Person 2 — Client/OpenCode:**

- Fork OpenCode, get it running locally, confirm a basic prompt→code session works with at least one provider key
- Read and document OpenCode's actual plugin/hook API in `docs/API_CONTRACTS.md` (append a section — what hook names exist, what arguments they receive) — this is exploratory, expect to spend real time here
- Scaffold the dashboard (`dashboard/`, React+Vite) with just a login/project-join screen — no real data yet

**End of Day 1 — hard requirement:** `docs/SCHEMA.md`, `docs/API_CONTRACTS.md`, and the env var list are pushed to `main` and considered frozen. Any change after this point needs a one-line note in the group chat, not a silent rename.

---

### Day 2 — Ingestion Pipeline

**Person A — GitHub → DB:**

- Build the `/projects/:id/github-webhook` route (Section 1): receive push event → fetch diff via GitHub REST API → run `diff_parser.py` (regex extraction of new routes, new dependencies, new env keys) → write to `commits` and `api_contracts` tables exactly per the frozen schema

**Person B — MCP server:**

- Build `engine/app/mcp_server.py` exposing the 6 tool functions from Section 1 exactly by name — wire them to real Postgres queries (not mock data anymore)
- Smoke-test by calling each tool manually (curl/Postman) against the Day 1 schema

**Handoff note must include:** whether the webhook is verified working against a real test push, and whether all 6 MCP tools return real data or are still partially mocked.

---

### Day 3 — Reasoning + Realtime

**Person A — Reasoning endpoint:**

- Build `retrieval.py` (Section 7 of master doc — always-on summary + targeted pgvector retrieval)
- Build `reasoning.py` — the only file that calls LiteLLM for user-facing answers, wired to the `/projects/:id/reason` route
- Add the `embedding` columns to `decisions`/`api_contracts` now, backfill embeddings for anything already in the DB

**Person B — Realtime + dashboard data:**

- Wire Supabase Realtime so dashboard subscribes to `tasks`, `decisions`, `events` tables and updates live
- Build the task board + decision log views in the dashboard, reading from the real API routes (Section 1), no more placeholder data

**Handoff note must include:** a real example prompt/response from the reasoning endpoint, pasted verbatim, so the next pair can see the actual output shape.

---

### Day 4 — Plugin Hooks + Conflict Prevention

**Person A — OpenCode plugin hooks:**

- Wire the "before agent writes code" hook to call `get_project_context()` / `get_api_contract()` via MCP and inject the result into the session (Section 4 of master doc, steps 2-4)
- Wire the "after a change" hook to call `report_change()` (steps 7-8)

**Person B — Conflict logic + action:**

- Build the specific conflict-prevention scenario you'll actually demo (Section 17 of master doc) — pick the exact feature (e.g. auth) and rehearse it working end to end now, don't leave this to Day 6
- Wire the auto-GitHub-issue action for when a conflict is flagged (`POST` to GitHub's issue API)

**Handoff note must include:** does the full loop (Dev A commits → Dev B's next prompt gets the right context) actually work across two separate machines, tested for real, not just localhost-to-localhost on one laptop.

---

### Day 5 — Task Assignment, Deadlines, Integration

**Person A — Task breakdown feature:**

- Extend the reasoning endpoint: a prompt like "add Google login" returns a suggested task breakdown + assignment, using real `GROUP BY`-style queries for "who's free" and "hours until deadline" (deterministic, not LLM-guessed — Section 9 of master doc)
- Wire "invite teammate" flow (however lightweight — even a shareable project-join link is enough)

**Person B — End-to-end integration pass:**

- Run the full flow (Section 4) across two actual separate laptops, not one machine simulating two — this is the first time it should be tested this way, and bugs here are expected
- Fix whatever breaks; update `docs/HANDOFF.md` with a precise list of what's still shaky

**Handoff note must include:** an honest go/no-go on whether the Section 17 demo scenario currently works end to end.

---

### Day 6 — Rehearsal & Buffer

**Person A — Demo rehearsal:**

- Run the exact Section 17 demo script 3+ times, on the actual hardware/network you'll use during judging if possible
- Record a backup video of it working, in case live networking fails during judging (Section 15 risk table, master doc)

**Person B — Fix & polish:**

- Fix whatever broke during rehearsal — nothing new gets built today, only stabilization
- Final pass on the dashboard so it looks presentable on the projector/screen judges will see

**End of Day 6:** `main` branch is what gets submitted. No commits after rehearsal confirms it works, unless something is actively broken.

---

## 3. Why This Stays Conflict-Free Across Different People/Days

1. **Contracts are frozen Day 1, not discovered incrementally** — nobody on Day 4 has to guess a column name or route path; it's already written down.
2. **Ownership is split by layer, not by feature** — one person is always "backend/data," the other "client/integration" each day, so two people are never editing the same files on the same day.
3. **`docs/HANDOFF.md` is mandatory, same day, every day** — a pair who wasn't there yesterday reads 5 lines and knows exactly where things stand, instead of re-deriving it from commit history.
4. **`main` is always demo-able** — nobody leaves it in a broken state overnight, so any day's pair can start from a working baseline.
