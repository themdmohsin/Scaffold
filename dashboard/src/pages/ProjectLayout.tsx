/**
 * ProjectLayout — the left-nav shell for one project. Loads the project
 * summary + membership (React Query; refresh survives navigation, so
 * deep-links and F5 just work) and renders the section nav. Children get the
 * project context via useProject().
 */

import { Suspense, createContext, useContext, type ReactNode } from "react";
import { NavLink, Outlet, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchProjects } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useProjectRealtime, type RealtimeHealth } from "../lib/useRealtime";
import { useTheme } from "../lib/theme";
import ErrorBoundary from "../components/ErrorBoundary";

interface ProjectCtx {
  projectId: string;
  role: "owner" | "admin" | "member";
  live: RealtimeHealth;
}

const ProjectContext = createContext<ProjectCtx | null>(null);

export function useProject(): ProjectCtx {
  const ctx = useContext(ProjectContext);
  if (!ctx) throw new Error("useProject must be used inside ProjectLayout");
  return ctx;
}

const NAV = [
  { to: "overview", label: "Overview" },
  { to: "tasks", label: "Tasks" },
  { to: "team", label: "Team" },
  { to: "environment", label: "Environment" },
  { to: "decisions", label: "Decisions" },
  { to: "contracts", label: "Contracts" },
  { to: "activity", label: "Activity" },
  { to: "settings", label: "Settings" },
];

export default function ProjectLayout() {
  const { projectId } = useParams<{ projectId: string }>();
  const { me, signOut } = useAuth();
  const { theme, toggle } = useTheme();

  // The membership list is the engine's verified GET /projects — role comes
  // from there (drives which controls render; the engine still enforces it).
  const projectsQ = useQuery({
    queryKey: ["projects"],
    queryFn: ({ signal }) => fetchProjects({ signal }),
    retry: false,
  });

  const live = useProjectRealtime(projectId);
  const project = projectsQ.data?.find((p) => p.id === projectId);
  const role = project?.supabase_role ?? "member";

  if (projectsQ.isLoading) {
    return (
      <div className="app-center" role="status">
        Opening project…
      </div>
    );
  }

  if (projectsQ.isError) {
    return (
      <div className="app-center">
        <div className="panel error-panel" role="alert">
          <p>Could not open this project: {projectsQ.error instanceof Error ? projectsQ.error.message : "unknown error"}</p>
          <div className="row-end">
            <button type="button" onClick={() => void projectsQ.refetch()}>
              Retry
            </button>
            <NavLink to="/projects" className="button ghost">
              All projects
            </NavLink>
          </div>
        </div>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="app-center">
        <div className="panel empty-state">
          <h1>Project not found</h1>
          <p>You may not be a member of this project, or the link is wrong.</p>
          <NavLink to="/projects" className="button primary">
            Your projects
          </NavLink>
        </div>
      </div>
    );
  }

  return (
    <ProjectContext.Provider value={{ projectId: projectId as string, role, live }}>
      <div className="shell">
        <header className="topbar project-topbar">
          <span className="brand">
            <NavLink to="/projects">Scaffold</NavLink>
          </span>
          <span className="project-title">
            <h1>{project.name}</h1>
            <span className={`live live-${live === "live" ? "on" : "off"}`} title="Realtime updates">
              ● {live === "live" ? "live" : live}
            </span>
          </span>
          <span className="topbar-user">
            <button type="button" className="ghost" onClick={toggle} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
              {theme === "dark" ? "☀" : "☾"}
            </button>
            <span className="muted">{me?.email ?? ""}</span>
            <button type="button" className="ghost" onClick={() => void signOut()}>
              Sign out
            </button>
          </span>
        </header>
        <div className="project-body">
          <nav className="sidenav" aria-label="Project sections">
            <ul>
              {NAV.map((item) => (
                <li key={item.to}>
                  <NavLink to={item.to} className={({ isActive }) => (isActive ? "active" : "")}>
                    {item.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
          <main className="project-main" id="main">
            <ErrorBoundary>
              <Suspense fallback={<p className="panel">Loading…</p>}>
                <Outlet />
              </Suspense>
            </ErrorBoundary>
          </main>
        </div>
      </div>
    </ProjectContext.Provider>
  );
}

/** Small helper for pages that need the current user's roster name. */
export function WithProject({ children }: { children: ReactNode }) {
  return <ProjectContext.Consumer>{(ctx) => (ctx ? children : null)}</ProjectContext.Consumer>;
}
