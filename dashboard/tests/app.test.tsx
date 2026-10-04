/**
 * Core dashboard tests (requirement 9): auth gate, create project, invite
 * join. Each test boots the REAL App with a controllable Supabase session
 * mock and a mocked engine fetch — the routing, gating and query behavior
 * under test is production code.
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
import { makeFetchMock, meFixture, projectFixture, type MockRoute } from "./mocks";

// Controllable session for the statically-imported supabase module.
const sessionState = vi.hoisted(() => ({ current: null as unknown }));
vi.mock("../src/lib/supabase", async () => {
  const { buildSupabaseMock, fakeSession } = await import("./mocks");
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

describe("auth gate", () => {
  it("shows the login screen for signed-out users and never the app", async () => {
    sessionState.current = null;
    vi.stubGlobal("fetch", makeFetchMock([]));
    renderApp("/projects");
    await waitFor(() => expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument());
    expect(screen.queryByText("Your projects")).not.toBeInTheDocument();
  });

  it("renders the projects page for a signed-in user with memberships from /auth/me", async () => {
    vi.stubGlobal(
      "fetch",
      makeFetchMock([
        { match: (m, p) => m === "GET" && p === "/auth/me", respond: () => ({ status: 200, body: meFixture }) },
        { match: (m, p) => m === "GET" && p === "/projects", respond: () => ({ status: 200, body: { projects: [projectFixture] } }) },
      ]),
    );
    renderApp("/projects");
    await waitFor(() => expect(screen.getByText("Test Project")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /new project/i })).toBeInTheDocument();
  });

  it("deep links straight into a project route (no refresh-loses-state)", async () => {
    vi.stubGlobal("fetch", makeFetchMock(standardProjectRoutes()));
    renderApp("/projects/p1/overview");
    await waitFor(() => expect(screen.getByText("Who is working on what")).toBeInTheDocument());
  });
});

describe("create project wizard", () => {
  it("creates a project via POST /projects and shows the connect-your-client step", async () => {
    const created = { ...projectFixture, id: "p2", name: "Fresh Project" };
    const postSpy = vi.fn();
    vi.stubGlobal(
      "fetch",
      makeFetchMock([
        { match: (m, p) => m === "GET" && p === "/auth/me", respond: () => ({ status: 200, body: meFixture }) },
        {
          match: (m, p) => {
            if (m === "POST" && p === "/projects") {
              postSpy();
              return true;
            }
            return false;
          },
          respond: () => ({ status: 201, body: created }),
        },
      ]),
    );
    renderApp("/projects/new");
    await user.type(await screen.findByLabelText(/project name/i), "Fresh Project");
    await user.click(screen.getByRole("button", { name: /create project/i }));
    await waitFor(() => expect(screen.getByText(/is live/i)).toBeInTheDocument());
    // The wizard surfaces the exact client-connect instructions + project ID.
    expect(screen.getByText(/connect your client/i)).toBeInTheDocument();
    expect(screen.getByText("p2")).toBeInTheDocument();
    expect(postSpy).toHaveBeenCalled();
  });
});

describe("invite join", () => {
  it("redeems an invite at /join/:code and navigates into the returned project_id", async () => {
    const joinResponse = { user_id: "u1", project_id: "p2", name: "Dev Example", role: null, existing: false };
    const joinedProject = { ...projectFixture, id: "p2", name: "Joined Project" };
    vi.stubGlobal(
      "fetch",
      makeFetchMock([
        { match: (m, p) => m === "GET" && p === "/auth/me", respond: () => ({ status: 200, body: meFixture }) },
        { match: (m, p) => m === "POST" && p === "/projects/join", respond: () => ({ status: 201, body: joinResponse }) },
        // After joining, GET /projects includes the new membership.
        { match: (m, p) => m === "GET" && p === "/projects", respond: () => ({ status: 200, body: { projects: [joinedProject] } }) },
        ...projectReadRoutes("p2"),
      ]),
    );
    renderApp("/join/CODE123");
    await user.click(await screen.findByRole("button", { name: /join project/i }));
    // THE FIX: the response's project_id is honored — we land inside p2.
    await waitFor(() => expect(screen.getByText("Who is working on what")).toBeInTheDocument());
  });
});

// --- shared route fixtures --------------------------------------------------

function projectReadRoutes(pid = "p1"): MockRoute[] {
  return [
    { match: (m, p) => m === "GET" && p === `/projects/${pid}/context`, respond: () => ({ status: 200, body: contextFixture() }) },
    { match: (m, p) => m === "GET" && p === `/projects/${pid}/members`, respond: () => ({ status: 200, body: [memberFixture] }) },
    { match: (m, p) => m === "GET" && p === `/projects/${pid}/tasks`, respond: () => ({ status: 200, body: [] }) },
    { match: (m, p) => m === "GET" && p === `/projects/${pid}/coordination`, respond: () => ({ status: 200, body: coordinationFixture() }) },
  ];
}

function standardProjectRoutes(): MockRoute[] {
  return [
    { match: (m, p) => m === "GET" && p === "/auth/me", respond: () => ({ status: 200, body: meFixture }) },
    { match: (m, p) => m === "GET" && p === "/projects", respond: () => ({ status: 200, body: { projects: [projectFixture] } }) },
    ...projectReadRoutes("p1"),
  ];
}

import { contextFixture, coordinationFixture, memberFixture } from "./mocks";
