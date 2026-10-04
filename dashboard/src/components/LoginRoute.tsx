import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../lib/auth";
import Login from "./Login";

/**
 * Login route wrapper: signed-in users are bounced to their projects.
 * Login itself lives at /login so deep links to protected pages survive.
 */
export default function LoginRoute() {
  const { session } = useAuth();
  const location = useLocation();
  if (session === undefined) {
    return (
      <div className="app-center" role="status">
        Loading…
      </div>
    );
  }
  if (session) return <Navigate to="/projects" replace />;
  return <Login notice={location.state?.notice ?? null} />;
}
