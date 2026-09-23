# SCAFFOLD — Master Architecture & Build Document

*This supersedes all earlier drafts. This is the single source of truth the team builds from.*

Labels: **VERIFIED** = confirmed from current sources, **ESTIMATE** = calculated projection with stated assumptions, **RECOMMENDATION** = architectural judgment call.

---

## 1. One-Liner & Track

**SCAFFOLD — a single coding interface (forked from the open-source OpenCode agent) that gives every teammate's AI coding session live, shared awareness of the whole project — so agents stop inventing conflicting designs, and the team always knows who's doing what and what's left before the deadline.**

**Track: Sovereign AI.** Maps directly onto the organizers' own Data → Knowledge → Memory → Reasoning → Action model (see Section 9).

---

## 2. The Problem

Small teams building fast constantly lose shared context: decisions get made in chat and forgotten, nobody has a live picture of who's blocked on what, two people's AI agents independently invent incompatible APIs, and when the plan changes nobody can reconstruct why. Existing coding-agent memory (Cursor rules, Windsurf Cascade, `.clinerules`) is scoped to **one developer's local session** — it doesn't coordinate *across* people. Existing team PM tools (Jira, Asana, Linear) coordinate across people but aren't wired into anyone's actual coding agent, and assume a project that lives for months, not 48 hours.

---

## 3. The Product — One App, Not Two

**Earlier drafts assumed developers would use a separate "SCAFFOLD app" alongside their normal coding tool. That's wrong — nobody would use it.** The corrected plan:

**OpenCode is open source** (VERIFIED: terminal/IDE/desktop AI coding agent, multi-provider — Claude, GPT, Gemini, BYOK — with built-in MCP server support and plugin hooks around tool execution and session events, server architecture supporting multiple clients). We fork it, rebrand it as Scaffold, and add:

- A plugin that hooks into "before the agent writes code" and "after a change is made"
- Those hooks silently call SCAFFOLD's own backend — the developer never sees a second app or a context switch

Everyone on the team uses this one app to write code, exactly like they'd use Claude Code or Cursor. Coordination happens invisibly underneath.

**Fallback if the fork proves too time-costly mid-build:** since OpenCode, Claude Code, and Cursor all already support adding any MCP server via config, a teammate can just paste SCAFFOLD's server URL into their existing tool's settings and get the same automatic coordination with zero app-building — less "branded," equally functional. Keep this as your Plan B, don't lead with it in the deck.

---

## 4. Canonical Flow — Exact, Corrected

Two separate LLM calls happen on every coding action, using two separate keys, and they never cross paths.

```
1. Dev types in the Scaffold (OpenCode-forked) interface:
   "connect the login button to the backend"

2. Plugin hook fires BEFORE the coding agent runs
      → asks SCAFFOLD's engine (the shared backend server) for
        relevant context

3. Engine retrieves context from Postgres/pgvector (Section 7) —
   the small always-on project summary + targeted retrieval for
   this specific request (Section 8). If the engine itself needs
   to reason/compress here, THAT is a small internal LLM call via
   LiteLLM, paid by SCAFFOLD's own TEAM key. This is SCAFFOLD
   thinking, not the developer's coding call.

4. Engine returns a context package (plain data) to the client

5. The Scaffold/OpenCode client sends the developer's prompt +
   that context DIRECTLY to whichever provider the developer
   configured, using the DEVELOPER'S OWN key, through OpenCode's
   existing model routing. LiteLLM is NOT involved in this hop.

6. That LLM (dev's own key) generates the actual code — shown in
   the interface exactly like normal OpenCode/Claude Code

7. Plugin hook fires AFTER the change
      → sends the diff/summary back to SCAFFOLD's engine

8. Engine updates the shared database (also cross-checked against
   the GitHub webhook once it's pushed — Section 6).
   If a clean one-line summary is wanted, that's another small
   internal LiteLLM call on the TEAM key.
      → broadcasts the update via Supabase Realtime
      → every teammate's dashboard updates within seconds

9. The NEXT prompt any other agent makes will pick up this new
   context via step 2-4. A session already mid-generation when
   step 8 fires does not update retroactively — see the honesty
   note in Section 5.
```

**Key invariant, keep this in the deck explicitly:** each developer's own API key pays for their own actual coding (step 5-6) — SCAFFOLD never touches that call. SCAFFOLD's team key only ever pays for its own small coordination calls (steps 3 and 8), which is why the cost stays negligible (Section 11).

---

## 5. What This Architecture Actually Solves — Stated Honestly

| Claim | True? | Why |
| --- | --- | --- |
| Prevents semantic/design conflicts (two agents inventing incompatible APIs) | **Yes** | Step 2-4: agent gets the real contract before writing, not after |
| Detects conflicts that slip through anyway | **Yes** | Git-diff-based detection (Section 6) as the safety net |
| Resolves literal Git merge conflicts (same lines, same file, edited by two people) | **No — and don't claim it does** | Still Git's job, same as any team. SCAFFOLD reduces *how often* this happens by preventing divergent designs upstream, but doesn't touch line-level merges |
| Shows a change to everyone's dashboard instantly | **Yes** | Supabase Realtime, seconds-level |
| Gives an *already-running* agent session mid-task awareness of a change that just landed | **No, not mid-generation** | Propagates on the agent's *next* prompt/hook trigger, not instantly into an in-progress generation. State this plainly — it's honest, not a weakness |
| Tracks deadlines, task ownership, who's free | **Yes** | Plain database fields + simple math, not AI guesswork |

---

## 6. Data & Ingestion

**Storage — RECOMMENDATION:** Postgres + pgvector, in the same Supabase instance already used elsewhere on the team's projects. No separate vector DB, no graph DB — the dependency structure here is shallow enough that a join table covers it (VERIFIED: Supabase ships pgvector as a one-line extension, no extra infrastructure).

**Tables:** projects, users, tasks (with owner, dependencies via a junction table, due date), decisions (text, reasoning, who, when, `affects` list of tasks/APIs), API contracts (route, method, schema as JSON), commits/PRs (SHA, message, files touched), blockers, event log.

**Ingestion — two channels:**

- **Plugin hooks** (Section 4, steps 2 & 7) — real-time, tied to the actual coding session
- **GitHub webhook** (push/PR events) + REST API to fetch the diff — the authoritative, deterministic cross-check. **VERIFIED reasoning, confirmed correct:** Git diff is deterministic; asking an LLM "what did you change" is not — use diff-based structural extraction (regex-level: new route strings, new lines in `requirements.txt`/`package.json`, new keys in `.env.example`) as ground truth, and reserve the LLM only for turning that diff into a one-sentence human-readable summary, never for deciding what changed.

**What's never stored raw:** full source file contents (store paths + diff summaries — Git already stores the code). **What's summarized, not stored verbatim:** long decision discussions — store the decision + reasoning, not the whole back-and-forth.

---

## 7. Context Engineering (the most important technical problem here)

**RECOMMENDATION:** never send the whole project state to a model.

- **Always-on summary** (attached to nearly every call): goal, current phase, each person's active task, the 5-10 most relevant API contracts, last handful of decisions. ~1-2K tokens.
- **Targeted retrieval on top of that**, specific to the current request, via pgvector similarity + metadata filtering — not a full dump.
- **GitHub awareness is similarly split:** the webhook connection is live at all times (costs nothing in tokens — it's just an API call), but a file's actual content is only pulled when a specific question genuinely needs it. Persistent awareness that something changed ≠ constantly loading full repo content into every prompt.

This keeps both cost (Section 11) and reasoning quality in check — a model reasoning over 2K focused tokens outperforms one wading through 50K irrelevant ones.

---

## 8. MCP — Where It Fits

**VERIFIED:** MCP (Model Context Protocol), open-sourced by Anthropic in Nov 2024, is now under the Linux Foundation's Agentic AI Foundation and is the de facto standard for agent↔tool connections — broadly supported across Claude, Cursor, OpenCode, and others. It is agent↔tool, not agent↔agent (that's the separate, less mature A2A/ACP space).

**Use it for:** exposing SCAFFOLD's functions to the coding agent — `get_project_context()`, `get_api_contract()`, `get_active_tasks()`, `get_recent_decisions()`, `report_change()`, `create_task()`. Any MCP-compatible client can call these with zero custom integration work — this is a real, citable technical advantage.

**Not for:** the umbrella reasoning itself (plain LLM call over retrieved context) or the BYOK multi-model routing (Section 9) — those are separate concerns.

---

## 9. The Umbrella AI's Responsibilities — Classified

| Responsibility | Classification |
| --- | --- |
| Detect a new API route from a diff | **A. Deterministic** — regex/pattern match |
| Detect two different routes for the same intent | **C. LLM reasoning** on top of A's structured input |
| Retrieve relevant context | **B. Retrieval** — pgvector + filters, not a fresh LLM call each time |
| "What should I work on" / task breakdown for a new feature | **C. LLM reasoning**, over B's retrieved context + deterministic facts (who's free, hours left) |
| Bottleneck detection (N tasks depend on one person) | **A. Deterministic** — a SQL `GROUP BY`, not AI |
| Auto-create a GitHub issue for a detected conflict | **D. Agent/tool execution** |
| Whether to actually change scope/architecture mid-hackathon | **E. Human decision** — SCAFFOLD informs, never decides |

**Principle:** anything answerable by a database query stays a database query — faster, free, 100% reliable for the live demo. The LLM is reserved for genuinely semantic judgments: naming conflicts, task breakdown, "what should I work on," diff summarization.

---

## 10. Multi-Model / BYOK

**RECOMMENDATION:** LiteLLM as the abstraction layer for SCAFFOLD's own internal calls (Section 4, steps 3 & 8) — single interface across Anthropic/OpenAI/Gemini, don't hand-roll three SDKs. The developer's own coding calls (step 5-6) go through OpenCode's existing native provider routing, untouched by SCAFFOLD or LiteLLM.

**Security:** SCAFFOLD's team key lives server-side, in memory for the session, never logged, not persisted at rest for the hackathon. Developer's own keys are configured inside their own OpenCode client exactly as OpenCode already handles it (VERIFIED: stored locally via `opencode auth login`, e.g. `~/.local/share/opencode/auth.json`) — SCAFFOLD's backend never sees or touches those.

---

## 11. Cost — VERIFIED rates + ESTIMATE for SCAFFOLD's overhead

**VERIFIED current API rates (per million tokens, input/output), checked September 2026:**

| Model | Input | Output | Source |
| --- | --- | --- | --- |
| Claude Haiku 4.5 | $1.00 | $5.00 | Anthropic official pricing docs |
| Claude Sonnet 5 | $2.00 | $10.00 | Anthropic official pricing docs (permanent, no scheduled increase) |
| GPT-5 | ~$1.25 | ~$10.00 | Secondary sources citing OpenAI's official pricing page |
| Gemini 2.5 Pro | $1.25 (≤200K ctx) | $10.00 | Google's official Gemini API pricing |

**ESTIMATE — SCAFFOLD's own overhead only** (Section 4 steps 3 & 8 — separate from each developer's normal coding usage, which they pay for regardless), assuming ~3K input / ~300 output tokens per internal call, Haiku 4.5:

| Scenario | Calls | Cost (Haiku 4.5) | Cost (Sonnet 5) |
| --- | --- | --- | --- |
| 4 devs, 24hr hackathon, heavy usage | ~200 | **$0.90** | $1.80 |
| 4 devs, 6-day build (dogfooding while building) | ~500 | **$2.25** | $4.50 |

**Bottom line: a few dollars total, regardless of model choice.** Put this in the deck as reassurance, not a risk.

---

## 12. Tech Stack

- **Client/interface:** Forked OpenCode (TypeScript/Go), rebranded, with a custom plugin hooking session/tool-execution events into SCAFFOLD's backend
- **Backend:** FastAPI
- **Database:** Supabase Postgres + pgvector
- **Realtime:** Supabase Realtime
- **Dashboard** (invite team, see tasks/decisions/deadlines): React + Vite, or built as OpenCode's own custom UI surface if time allows
- **GitHub integration:** webhooks + REST API
- **Agent integration:** MCP server exposing the Section 8 functions
- **Multi-model abstraction (SCAFFOLD's internal calls only):** LiteLLM

---

## 13. What NOT to Build

- A coding agent engine from scratch (fork OpenCode instead — Section 3)
- Graph database — a Postgres join table covers the actual dependency depth
- A second vector database — pgvector in the same Postgres instance is enough
- Full AST/tree-sitter analysis — regex-level diff pattern matching is enough for the demo
- Autonomous task allocation — SCAFFOLD recommends, humans assign
- Automatic code merging or resolving literal Git merge conflicts — explicitly out of scope (Section 5)
- Kafka, NATS, Kubernetes, a custom LLM, fine-tuning — disproportionate to this scale
- Production-grade key vault/rotation, tenant isolation, sandboxed execution — real concerns for a later version, explicitly out of scope for a hackathon prototype

---

## 14. Competitive Landscape

- **Cursor / Windsurf / Cline / Aider** — already maintain project memory, but scoped to **one developer's local IDE session**, not shared live across a team using different tools/models. None coordinate *between* team members or prevent cross-developer design conflicts.
- **GitHub Copilot / Devin** — single-developer inline assistance / autonomous single-agent coding. Neither addresses multi-developer coordination.
- **Linear / Notion / Jira / Asana** — team-facing, but built for month-long projects with onboarding overhead, and not wired into anyone's actual coding agent — they track tickets, not what an agent actually built.
- **Independent OSS attempts** (e.g. small GitHub projects doing "AI memory layer for switching coding agents") confirm the pain point is real, but are scoped to one developer's own context, not a live multi-person shared state.

**Honest claim for the deck:** no single existing product does live, shared, cross-developer, cross-model project coordination built into one interface, purpose-built for a project whose entire life is 24-48 hours. The pieces exist separately (per-developer IDE memory, team PM tools, MCP as the connective protocol, OpenCode as an open coding-agent base to build on) — SCAFFOLD's innovation is assembling them for this specific case, not inventing any one piece from scratch. State this plainly rather than implying an empty market.

---

## 15. Top Risks

| Risk | Mitigation |
| --- | --- |
| Forking/understanding OpenCode's plugin system eats a full day | Start Day 1, have the plain-MCP-config fallback (Section 3) ready |
| GitHub webhook/auth setup friction | Start Day 1 in parallel; PAT fallback if a GitHub App stalls |
| Realtime misconfiguration causes silent sync failures | Have polling tested as fallback |
| Regex-based conflict detection misses the exact scenario shown live | Hand-pick and rehearse the specific demo conflict — don't rely on general detection working live untested |
| Context package too large/unfocused → vague reasoning output | Build and test the retrieval pipeline (Section 7) early, not last |
| Team overclaims "we fix merge conflicts" to judges | Use the exact honest framing in Section 5 |
| A laptop/network fails during the live multi-developer demo | Record a backup clip of the working demo as fallback |

---

## 16. 6-Day Build Plan (4 people)

- **Day 1:** Fork OpenCode, get it running; Supabase schema (Section 6); GitHub webhook skeleton; FastAPI backend running. Split: 1 on OpenCode fork/plugin scaffolding, 1 on DB schema/backend, 1 on GitHub integration, 1 on dashboard skeleton.
- **Day 2:** Plugin hooks wired end-to-end (before/after code change → engine); MCP server exposing `get_project_context` / `report_change`.
- **Day 3:** LLM reasoning endpoint ("what should I work on" / task breakdown) wired to real retrieved context; Realtime pushing updates to dashboard.
- **Day 4:** Conflict-prevention flow for the specific demo scenario (Section 17) — build this deliberately, don't hope the general system catches it live.
- **Day 5:** Decision logging, deadline/task UI polish, auto-GitHub-issue action.
- **Day 6:** Full demo run-through, fix what breaks, record a backup video.

---

## 17. The Demo ("Wow Moment")

Two teammates, live, on their own laptops, both inside the same Scaffold app:

1. Dev A prompts "add Google login" → SCAFFOLD proposes a task breakdown and assigns based on real availability — visible instantly on both dashboards.
2. Dev A's agent builds the auth endpoint. The plugin hook silently registers the new API contract.
3. Dev B prompts "connect the login button" — their agent automatically receives the real contract (Section 4, step 2-4) and uses the correct route without being told.
4. Deliberately trigger a case where an agent *would have* diverged — show the before/after: without the hook, it invents a different path; with it, it doesn't.

That's the entire pitch in under a minute, honestly scoped to what's actually built.

---

## 18. Deck Structure Checklist (9 required sections, per ASYNC'26 guide)

1. Project name → Section 1
2. Team details → fill in separately
3. Selected track → Sovereign AI (Section 1)
4. Problem → Section 2
5. Project idea → Sections 1, 3
6. Solution & how it works → Sections 3-5, 17
7. Architecture/workflow → Section 4 diagram + Sections 6-10
8. Technologies → Section 12
9. What makes it different → Section 14

**Judging weight reminder:** 30% Technical Execution (lean on Sections 4-10 being concrete and real), 20% Innovation (Section 14's honest positioning), 20% Impact (frame beyond hackathons — any small fast-moving team), 15% Product Experience (the one-app decision in Section 3 directly serves this), 15% Demo & Completeness (Section 17, scoped honestly per Section 5).
