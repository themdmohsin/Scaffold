/**
 * Shared test harness: a controllable Supabase session mock + a mocked engine
 * fetch.
 *
 * The session mock is consumed from test files via vi.mock's async factory
 * (the ONLY reliable way to mock a statically-imported module):
 *
 *   const sessionState = vi.hoisted(() => ({ current: null as unknown }));
 *   vi.mock("../src/lib/supabase", async () => {
 *     const { buildSupabaseMock } = await import("./mocks");
 *     return buildSupabaseMock(sessionState);
 *   });
 *
 * Tests then flip sessionState.current between a fake session and null.
 */

export const fakeSession = {
  user: { id: "account-1", email: "dev@example.com" },
  access_token: "fake-jwt",
};

export interface SessionState {
  current: unknown;
}

/**
 * The module shape lib/supabase exports, driven by sessionState.current.
 * The client object itself is always present (consumers read it once); the
 * SESSION inside getSession is read lazily so tests can flip sign-in state
 * between tests.
 */
export function buildSupabaseMock(state: SessionState) {
  return {
    supabase: {
      auth: {
        getSession: async () => ({ data: { session: state.current }, error: null }),
        onAuthStateChange: () => ({ data: { subscription: { unsubscribe: () => undefined } } }),
        signOut: async () => ({ error: null }),
      },
      // No-op realtime: lib/realtime needs a chainable channel builder.
      channel: () => {
        const ch = {
          on: () => ch,
          subscribe: (cb?: (status: string) => void) => {
            cb?.("SUBSCRIBED");
            return ch;
          },
        };
        return ch;
      },
      removeChannel: async () => undefined,
    },
    authConfigured: true,
    getAccessToken: async () => (state.current ? (state.current as { access_token: string }).access_token : null),
    oauthErrorFromUrl: () => null,
  };
}

export interface MockRoute {
  match: (method: string, path: string) => boolean;
  respond: () => { status: number; body: unknown };
}

export function makeFetchMock(routes: MockRoute[]) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = url.replace(/^https?:\/\/[^/]+/, "");
    const method = (init?.method ?? "GET").toUpperCase();
    for (const route of routes) {
      if (route.match(method, path)) {
        const { status, body } = route.respond();
        return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
      }
    }
    return new Response(JSON.stringify({ detail: `no mock for ${method} ${path}` }), { status: 404 });
  });
}

import { vi } from "vitest";

/** Standard engine fixtures shared by several tests. */
export const meFixture = {
  account_id: "account-1",
  via: "jwt",
  email: "dev@example.com",
  full_name: "Dev Example",
  auth_provider: "email",
  token_scoped_project_id: null,
  memberships: [{ project_id: "p1", supabase_role: "owner", joined_at: "2026-10-01T00:00:00Z" }],
};

export const projectFixture = {
  id: "p1",
  name: "Test Project",
  goal: "goal",
  deadline: null,
  created_at: "2026-10-01T00:00:00Z",
  supabase_role: "owner",
  joined_at: "2026-10-01T00:00:00Z",
};

export const memberFixture = {
  id: "u1",
  name: "Dev Example",
  role: "backend",
  kind: "developer",
  agent_provider: null,
  agent_model: null,
  membership_status: "active",
  joined_at: "2026-10-01T00:00:00Z",
  current_task: null,
  activity_status: "ACTIVE",
  last_activity_at: null,
  is_me: true,
};

export function contextFixture(project = projectFixture) {
  return {
    project,
    tasks: { todo: 0, in_progress: 0, done: 0 },
    active_tasks: [],
    recent_decisions: [],
    relevant_contracts: [],
    blockers: [],
    recent_events: [],
    generated_at: "2026-10-01T00:00:00Z",
    task_counts: { todo: 0, in_progress: 0, review: 0, done: 0, blocked: 0 },
    open_conflicts: 0,
  };
}

export function coordinationFixture(overrides: Record<string, unknown> = {}) {
  return {
    project_id: "p1",
    task_states: { READY: 0, BLOCKED: 0, WAITING_ON_DEPENDENCY: 0, IN_PROGRESS: 0, REVIEW: 0, DONE: 0 },
    ready_to_start: [],
    blocked: [],
    needs_review: [],
    conflicts: { open_contract_conflicts: [], task_overlaps: [], contract_collisions: [], cross_owner_dependencies: [] },
    recommended_next_step: { kind: "all_clear", title: "All clear", task: null, reasons: [] },
    who_is_doing_what: [],
    generated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}
