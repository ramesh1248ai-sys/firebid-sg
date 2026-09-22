import { NavLink, Outlet } from "react-router";

import { cn } from "@/lib/utils";

const navItems = [
  { to: "/", label: "Home", end: true },
  { to: "/sign-in", label: "Sign in", end: false },
];

export function AppShell() {
  return (
    <div className="flex min-h-svh flex-col">
      <header className="border-b">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-8 px-6">
          <span className="font-semibold tracking-tight">
            <span className="text-brand">Fire</span>Bid SG
          </span>
          <nav aria-label="Main" className="flex gap-6 text-sm">
            {navItems.map((item) => (
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
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">
        <Outlet />
      </main>
    </div>
  );
}
