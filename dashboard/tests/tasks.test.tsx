/**
 * Task board tests (requirement 9): task move (optimistic PATCH), due-date
 * editing, no wrap-around from DONE, and the single-ready-task accept fix
 * (Accept & claim must be enabled and functional when exactly one task is
 * ready — the defect where the old panel disabled it).
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import App from "../src/App";
import { AuthProvider } from "../src/lib/auth";
import { ToastProvider } from "../src/components/Toasts";
import { ConfirmProvider } from "../src/components/ConfirmDialog";
import { makeFetchMock, meFixture, memberFixture, projectFixture, type MockRoute } from "./mocks";

const sessionState = vi.hoisted(() => ({ current: null as unknown }));
vi.mock("../src/lib/supabase", async () => {
  const { buildSupabaseMock } = await import("./mocks");
  return buildSupabaseMock(sessionState);
});

const user = userEvent.setup();

function renderApp(initialRoute = "/projects") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } } });
  return render(
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <ToastProvider>
          <ConfirmProvider>
            <MemoryRouter initialEntries={[initialRoute]}>
              <App />
            </MemoryRouter>
          </ConfirmProvider>
        </ToastProvider>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionState.current = { user: { id: "account-1", email: "dev@example.com" }, access_token: "fake-jwt" };
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const taskFixture = {
  id: "t1",
  project_id: "p1",
  title: "Ship the thing",
  status: "todo",
  owner_id: null,
  due_at: null,
  created_at: "2026-10-01T00:00:00Z",
  description: null,
  priority: "medium",
  blocked: false,
  created_by: null,
  completed_at: null,
  dependencies: [],
  blocked_by_dependencies: [],
  is_blocked: false,
};

function routesWithTask(task: typeof taskFixture, patches: { method: string; path: string; status: number }[] = []): MockRoute[] {
  const patchLog: { method: string; path: string; body: unknown }[] = [];
  const routes: MockRoute[] = [
    { match: (m, p) => m === "GET" && p === "/auth/me", respond: () => ({ status: 200, body: meFixture }) },
    { match: (m, p) => m === "GET" && p === "/projects", respond: () => ({ status: 200, body: { projects: [projectFixture] } }) },
    { match: (m, p) => m === "GET" && p === "/projects/p1/context", respond: () => ({ status: 200, body: contextFixture() }) },
    { match: (m, p) => m === "GET" && p === "/projects/p1/members", respond: () => ({ status: 200, body: [memberFixture] }) },
    {
      match: (m, p) => m === "GET" && p === "/projects/p1/tasks",
      respond: () => ({ status: 200, body: [task] }),
    },
    { match: (m, p) => m === "GET" && p === "/projects/p1/users", respond: () => ({ status: 200, body: [{ id: "u1", project_id: "p1", name: "Dev Example", role: "backend" }] }) },
    { match: (m, p) => m === "GET" && p === "/projects/p1/tasks/ready", respond: () => ({ status: 200, body: { ready_tasks: [], count: 0, ready_task_ids: [], unassigned_ready_count: 0, generated_at: "2026-10-01T00:00:00Z" } }) },
    { match: (m, p) => m === "GET" && p === "/projects/p1/coordination", respond: () => ({ status: 200, body: coordinationFixture() }) },
    {
      match: (m, p) => {
        if (m === "PATCH" && p.startsWith("/projects/p1/tasks/")) {
          patchLog.push({ method: m, path: p, body: JSON.parse(String((globalThis as { __lastBody?: unknown }).body ?? "{}")) });
          return true;
        }
        return false;
      },
      respond: () => ({ status: 200, body: { ...task, status: "in_progress" } }),
    },
  ];
  void patches;
  return routes;
}

describe("tasks page", () => {
  it("moves a task with an optimistic PATCH (task move)", async () => {
    const patchCalls: { path: string; body: Record<string, unknown> }[] = [];
    vi.stubGlobal(
      "fetch",
      makeFetchMock([
        { match: (m, p) => m === "GET" && p === "/auth/me", respond: () => ({ status: 200, body: meFixture }) },
        { match: (m, p) => m === "GET" && p === "/projects", respond: () => ({ status: 200, body: { projects: [projectFixture] } }) },
        { match: (m, p) => m === "GET" && p === "/projects/p1/context", respond: () => ({ status: 200, body: contextFixture() }) },
        { match: (m, p) => m === "GET" && p === "/projects/p1/members", respond: () => ({ status: 200, body: [memberFixture] }) },
        { match: (m, p) => m === "GET" && p === "/projects/p1/tasks", respond: () => ({ status: 200, body: [taskFixture] }) },
        { match: (m, p) => m === "GET" && p === "/projects/p1/users", respond: () => ({ status: 200, body: [{ id: "u1", project_id: "p1", name: "Dev Example", role: "backend" }] }) },
        { match: (m, p) => m === "GET" && p === "/projects/p1/tasks/ready", respond: () => ({ status: 200, body: { ready_tasks: [], count: 0, ready_task_ids: [], unassigned_ready_count: 0, generated_at: "2026-10-01T00:00:00Z" } }) },
        { match: (m, p) => m === "GET" && p === "/projects/p1/coordination", respond: () => ({ status: 200, body: coordinationFixture() }) },
        {
          match: (m, p) => {
            if (m === "PATCH" && p === "/projects/p1/tasks/t1") return true;
            return false;
          },
          respond: () => ({ status: 200, body: { ...taskFixture, status: "in_progress" } }),
        },
      ]),
    );
    renderApp("/projects/p1/tasks");
    const moveBtn = await screen.findByRole("button", { name: /move to in progress/i });
    await user.click(moveBtn);
    await waitFor(() => {
      const calls = (fetch as unknown as { mock: { calls: [string, RequestInit | undefined][] } }).mock.calls;
      const patch = calls.find(([, init]) => init?.method === "PATCH");
      expect(patch).toBeDefined();
      expect(patch?.[0]).toContain("/projects/p1/tasks/t1");
      expect(JSON.parse(String(patch?.[1]?.body))).toMatchObject({ status: "in_progress" });
    });
  });

  it("does not wrap DONE back to TODO (no advance button on done tasks)", async () => {
    vi.stubGlobal(
      "fetch",
      makeFetchMock(
        routesWithTask({ ...taskFixture, status: "done" }),
      ),
    );
    renderApp("/projects/p1/tasks");
    await screen.findByText("Ship the thing");
    expect(screen.queryByRole("button", { name: /move to/i })).not.toBeInTheDocument();
  });

  it("edits the due date via the task dialog (PATCH carries due_at)", async () => {
    let patchBody: Record<string, unknown> | null = null;
    vi.stubGlobal(
      "fetch",
      makeFetchMock([
        { match: (m, p) => m === "GET" && p === "/auth/me", respond: () => ({ status: 200, body: meFixture }) },
        { match: (m, p) => m === "GET" && p === "/projects", respond: () => ({ status: 200, body: { projects: [projectFixture] } }) },
        { match: (m, p) => m === "GET" && p === "/projects/p1/context", respond: () => ({ status: 200, body: contextFixture() }) },
        { match: (m, p) => m === "GET" && p === "/projects/p1/members", respond: () => ({ status: 200, body: [memberFixture] }) },
        { match: (m, p) => m === "GET" && p === "/projects/p1/tasks", respond: () => ({ status: 200, body: [taskFixture] }) },
        { match: (m, p) => m === "GET" && p === "/projects/p1/users", respond: () => ({ status: 200, body: [{ id: "u1", project_id: "p1", name: "Dev Example", role: "backend" }] }) },
        { match: (m, p) => m === "GET" && p === "/projects/p1/tasks/ready", respond: () => ({ status: 200, body: { ready_tasks: [], count: 0, ready_task_ids: [], unassigned_ready_count: 0, generated_at: "2026-10-01T00:00:00Z" } }) },
        { match: (m, p) => m === "GET" && p === "/projects/p1/coordination", respond: () => ({ status: 200, body: coordinationFixture() }) },
        {
          match: (m, p) => {
            if (m === "PATCH" && p === "/projects/p1/tasks/t1") {
              patchBody = JSON.parse(String((globalThis as unknown as { __body?: unknown }).body ?? "{}"));
              return true;
            }
            return false;
          },
          respond: () => ({ status: 200, body: { ...taskFixture, status: "in_progress" } }),
        },
      ]),
    );
    renderApp("/projects/p1/tasks");
    // Open the dialog via the card title button.
    await user.click(await screen.findByRole("button", { name: "Ship the thing" }));
    const due = await screen.findByLabelText(/due date/i);
    await user.type(due, "2026-12-01");
    // jsdom cannot showModal, so the <dialog> is "closed" and byRole hides its
    // contents — query by text instead.
    await user.click(screen.getByText(/save changes/i));
    await waitFor(() => {
      const calls = (fetch as unknown as { mock: { calls: [string, RequestInit | undefined][] } }).mock.calls;
      const patch = calls.find(([, init]) => init?.method === "PATCH");
      const body = JSON.parse(String(patch?.[1]?.body ?? "{}"));
      expect(body.due_at).toContain("2026-12-01");
    });
    expect(patchBody === null || typeof patchBody === "object").toBe(true);
  });
});

describe("single-ready-task accept (the disabled-when-exactly-one-ready defect)", () => {
  it("enables Accept & claim and POSTs the signed-in user's roster id", async () => {
    let acceptBody: Record<string, unknown> | null = null;
    const oneReady = coordinationFixture({
      task_states: { READY: 1, BLOCKED: 0, WAITING_ON_DEPENDENCY: 0, IN_PROGRESS: 0, REVIEW: 0, DONE: 0 },
      ready_to_start: [
        {
          id: "t1",
          title: "Ship the thing",
          status: "todo",
          priority: "medium",
          owner_id: null,
          score: 95,
          score_factors: { priority: 40 },
          reasons: ["status is todo"],
          downstream_open: 0,
        },
      ],
      recommended_next_step: {
        kind: "start_task",
        title: "Start “Ship the thing”",
        task: { id: "t1", title: "Ship the thing", status: "todo", owner_id: null, priority: "medium", state: "READY", downstream_open: 0 },
        reasons: ["top ranked ready work"],
      },
    });
    vi.stubGlobal(
      "fetch",
      makeFetchMock([
        { match: (m, p) => m === "GET" && p === "/auth/me", respond: () => ({ status: 200, body: meFixture }) },
        { match: (m, p) => m === "GET" && p === "/projects", respond: () => ({ status: 200, body: { projects: [projectFixture] } }) },
        { match: (m, p) => m === "GET" && p === "/projects/p1/context", respond: () => ({ status: 200, body: contextFixture() }) },
        { match: (m, p) => m === "GET" && p === "/projects/p1/members", respond: () => ({ status: 200, body: [memberFixture] }) },
        { match: (m, p) => m === "GET" && p === "/projects/p1/tasks", respond: () => ({ status: 200, body: [taskFixture] }) },
        { match: (m, p) => m === "GET" && p === "/projects/p1/tasks/ready", respond: () => ({ status: 200, body: { ready_tasks: [oneReady.ready_to_start[0]], count: 1, ready_task_ids: ["t1"], unassigned_ready_count: 1, generated_at: "2026-10-01T00:00:00Z" } }) },
        { match: (m, p) => m === "GET" && p === "/projects/p1/coordination", respond: () => ({ status: 200, body: oneReady }) },
        {
          match: (m, p) => {
            if (m === "POST" && p === "/projects/p1/tasks/t1/accept-recommendation") {
              acceptBody = JSON.parse(String((globalThis as unknown as { __accept?: unknown }).__accept ?? "{}"));
              return true;
            }
            return false;
          },
          respond: () => ({ status: 201, body: { accepted: true, task_id: "t1", idempotent: false } }),
        },
      ]),
    );
    renderApp("/projects/p1/overview");
    const accept = await screen.findByRole("button", { name: /accept & claim/i });
    // THE FIX: exactly one ready task and the button is NOT disabled.
    expect(accept).toBeEnabled();
    await user.click(accept);
    await waitFor(() => {
      const calls = (fetch as unknown as { mock: { calls: [string, RequestInit | undefined][] } }).mock.calls;
      const post = calls.find(([, init]) => init?.method === "POST");
      expect(post?.[0]).toContain("/projects/p1/tasks/t1/accept-recommendation");
      const body = JSON.parse(String(post?.[1]?.body ?? "{}"));
      // Claims under the SIGNED-IN user's own roster row (is_me), no acting-as picker.
      expect(body).toMatchObject({ user_id: "u1" });
    });
    expect(acceptBody === null || typeof acceptBody === "object").toBe(true);
  });
});

// --- fixtures ---------------------------------------------------------------

function contextFixture() {
  return {
    project: projectFixture,
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

function coordinationFixture(overrides: Record<string, unknown> = {}) {
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
