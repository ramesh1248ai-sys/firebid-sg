import { ArrowLeft, Flame, LogOut } from "lucide-react";
import { Link, NavLink, Outlet, useLocation } from "react-router";

import { useAuth } from "@/auth/session";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const navItems = [
  { to: "/", label: "Bids", end: true },
  { to: "/library", label: "Library", end: false },
  { to: "/rates", label: "Rates", end: false },
  { to: "/kpis", label: "KPIs", end: false },
  { to: "/audit", label: "History", end: false },
];

// The parts of a bid, in the order the work goes through them.
const bidSections = [
  { to: "documents", label: "Documents" },
  { to: "registers", label: "Registers" },
  { to: "symbols", label: "Symbols" },
  { to: "specification", label: "Specification" },
  { to: "design", label: "Design" },
  { to: "workbench", label: "Workbench" },
  { to: "boq", label: "BOQ" },
];

// Only the roles the API would let in; otherwise the link leads to a refusal.
const ADMIN_ROLES = ["system_admin", "commercial_director"];

function initials(name: string): string {
  const words = name.trim().split(/\s+/);
  return ((words[0]?.[0] ?? "") + (words.length > 1 ? (words.at(-1)?.[0] ?? "") : "")).toUpperCase();
}

/** Which bid a page inside a bid belongs to: `/bids/<id>/<anything>`, not the bid itself. */
function bidOf(pathname: string): string | null {
  const found = /^\/bids\/([^/]+)\/.+/.exec(pathname);
  return found && found[1] !== "new" ? (found[1] ?? null) : null;
}

/**
 * Above every page of a bid: the way back to the bid, and across to its other parts. A page
 * can run to hundreds of rows, so the way out is at the top, where a person arrives.
 */
function BidBar({ bidId, wide }: { bidId: string; wide: boolean }) {
  return (
    <div className="border-b bg-card">
      <div
        className={cn(
          "mx-auto flex items-center gap-6 overflow-x-auto",
          wide ? "max-w-none px-4" : "max-w-7xl px-6",
        )}
      >
        <Link
          to={`/bids/${bidId}`}
          className="flex shrink-0 items-center gap-1.5 py-2.5 text-sm font-medium text-link hover:underline"
        >
          <ArrowLeft className="size-4" aria-hidden />
          Back to the bid
        </Link>
        <nav aria-label="Bid sections" className="flex gap-1 border-l pl-4 text-sm">
          {bidSections.map((section) => (
            <NavLink
              key={section.to}
              to={`/bids/${bidId}/${section.to}`}
              className={({ isActive }) =>
                cn(
                  "-mb-px border-b-2 border-transparent px-3 py-2.5 whitespace-nowrap text-muted-foreground transition-colors hover:text-foreground",
                  isActive && "border-primary font-medium text-foreground",
                )
              }
            >
              {section.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </div>
  );
}

export function AppShell() {
  const { session, status, signOut } = useAuth();
  const { pathname } = useLocation();
  // The workbench is a drawing and two panels: it takes the whole width a screen has.
  const wide = pathname.endsWith("/workbench");
  const bidId = status === "signed-in" ? bidOf(pathname) : null;
  // The bid list, a bid's own page and sign-in lay out their own surfaces; every other page
  // is one sheet of content on the canvas.
  const ownLayout =
    wide ||
    pathname === "/" ||
    (/^\/bids\/[^/]+$/.test(pathname) && pathname !== "/bids/new") ||
    status !== "signed-in";

  return (
    <div className="flex min-h-svh flex-col">
      <header className="bg-header text-header-foreground shadow-sm">
        <div
          className={cn(
            "mx-auto flex h-14 items-center gap-8",
            wide ? "max-w-none px-4" : "max-w-7xl px-6",
          )}
        >
          <span className="flex items-center gap-2 text-[15px] font-semibold tracking-tight">
            <span className="grid size-7 place-items-center rounded-md bg-brand text-white">
              <Flame className="size-4" aria-hidden />
            </span>
            <span>
              FireBid <span className="font-normal text-header-muted">SG</span>
            </span>
          </span>
          {status === "signed-in" && (
            <nav aria-label="Main" className="flex gap-1 text-sm">
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
                      "rounded-md px-3 py-1.5 text-header-muted transition-colors hover:bg-white/10 hover:text-header-foreground",
                      // A bid's pages are under Bids.
                      (isActive || (item.to === "/" && pathname.startsWith("/bids"))) &&
                        "bg-white/15 font-medium text-header-foreground",
                    )
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </nav>
          )}
          {session && (
            <div className="ml-auto flex items-center gap-3 text-sm">
              <span
                aria-hidden
                className="grid size-8 place-items-center rounded-full bg-white/15 text-xs font-semibold"
              >
                {initials(session.name)}
              </span>
              <span className="text-header-foreground">{session.name}</span>
              <Button
                variant="ghost"
                size="sm"
                className="text-header-muted hover:bg-white/10 hover:text-header-foreground"
                onClick={() => void signOut()}
              >
                <LogOut aria-hidden />
                Sign out
              </Button>
            </div>
          )}
        </div>
      </header>
      {bidId && <BidBar bidId={bidId} wide={wide} />}
      <main
        className={cn(
          "mx-auto w-full flex-1",
          // The workbench's panels sit on white, edge to edge.
          wide ? "max-w-none bg-card px-4 py-4" : "max-w-7xl px-6 py-8",
        )}
      >
        {ownLayout ? (
          <Outlet />
        ) : (
          <div className="fb-card p-6 sm:p-8">
            <Outlet />
          </div>
        )}
      </main>
      {!wide && (
        <footer className="border-t bg-card">
          <div className="mx-auto max-w-7xl px-6 py-3 text-xs text-muted-foreground">
            FireBid SG · tendering for fire protection contractors
          </div>
        </footer>
      )}
    </div>
  );
}
