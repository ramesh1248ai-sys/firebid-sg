import { NavLink, Outlet } from "react-router";

import { useAuth } from "@/auth/session";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const navItems = [
  { to: "/", label: "Bids", end: true },
  { to: "/library", label: "Library", end: false },
  { to: "/audit", label: "History", end: false },
];

// Only the roles the API would let in; otherwise the link leads to a refusal.
const ADMIN_ROLES = ["system_admin", "commercial_director"];

export function AppShell() {
  const { session, status, signOut } = useAuth();

  return (
    <div className="flex min-h-svh flex-col">
      <header className="border-b">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-8 px-6">
          <span className="font-semibold tracking-tight">
            <span className="text-brand">Fire</span>Bid SG
          </span>
          {status === "signed-in" && (
            <nav aria-label="Main" className="flex gap-6 text-sm">
              {[
                ...navItems,
                ...(session?.roles.some((role) => ADMIN_ROLES.includes(role))
                  ? [{ to: "/admin", label: "Platform", end: false }]
                  : []),
              ].map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    cn(
                      "text-muted-foreground transition-colors hover:text-foreground",
                      isActive && "font-medium text-foreground",
                    )
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </nav>
          )}
          {session && (
            <div className="ml-auto flex items-center gap-4 text-sm">
              <span className="text-muted-foreground">{session.name}</span>
              <Button variant="outline" size="sm" onClick={() => void signOut()}>
                Sign out
              </Button>
            </div>
          )}
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">
        <Outlet />
      </main>
    </div>
  );
}
