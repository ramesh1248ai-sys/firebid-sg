import { Navigate, Outlet, useLocation } from "react-router";

import { useAuth } from "@/auth/session";

/** Keeps a route for signed-in people, and remembers where they were heading. */
export function RequireAuth() {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Checking your sign-in…
      </p>
    );
  }
  if (status === "signed-out") {
    return <Navigate to="/sign-in" replace state={{ returnTo: location.pathname }} />;
  }
  return <Outlet />;
}
