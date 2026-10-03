/**
 * App shell — router + auth gate. Route guards live here: signed-out users
 * see the Login screen (with any deep-link destination preserved); signed-in
 * users get the app with the left-nav project layout. Session restoration is
 * awaited before rendering anything so a refresh never flashes the login.
 */

import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./lib/auth";
import LoginRoute from "./components/LoginRoute";
import ProjectListPage from "./pages/ProjectListPage";
import CreateProjectPage from "./pages/CreateProjectPage";
import JoinPage from "./pages/JoinPage";
import ProjectLayout from "./pages/ProjectLayout";
import OverviewPage from "./pages/OverviewPage";
import TasksPage from "./pages/TasksPage";
import TeamPage from "./pages/TeamPage";
import EnvironmentPage from "./pages/EnvironmentPage";
import DecisionsPage from "./pages/DecisionsPage";
import ContractsPage from "./pages/ContractsPage";
import ActivityPage from "./pages/ActivityPage";
import SettingsPage from "./pages/SettingsPage";

/** Signed-in-only gate. Remembers where the user was heading. */
function RequireAuth() {
  const { session } = useAuth();
  const location = useLocation();
  if (session === undefined) {
    return (
      <div className="app-center" role="status">
        Loading…
      </div>
    );
  }
  if (session === null) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <Outlet />;
}

/** Signed-in users never see /login — bounced by the /login route itself. */

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginRoute />} />
      <Route element={<RequireAuth />}>
        <Route path="/" element={<Navigate to="/projects" replace />} />
        <Route path="/projects" element={<ProjectListPage />} />
        <Route path="/projects/new" element={<CreateProjectPage />} />
        <Route path="/join/:code" element={<JoinPage />} />
        <Route path="/join" element={<JoinPage />} />
        <Route path="/projects/:projectId" element={<ProjectLayout />}>
          <Route index element={<Navigate to="overview" replace />} />
          <Route path="overview" element={<OverviewPage />} />
          <Route path="tasks" element={<TasksPage />} />
          <Route path="team" element={<TeamPage />} />
          <Route path="environment" element={<EnvironmentPage />} />
          <Route path="decisions" element={<DecisionsPage />} />
          <Route path="contracts" element={<ContractsPage />} />
          <Route path="activity" element={<ActivityPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
      </Route>
      <Route
        path="*"
        element={
          <div className="app-center">
            <h1>Page not found</h1>
            <a href="/projects">Go to your projects</a>
          </div>
        }
      />
    </Routes>
  );
}
