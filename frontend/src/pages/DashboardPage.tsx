import { useQuery } from "@tanstack/react-query";
import { CalendarClock, FolderOpen, ListTodo, Plus, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";

import { api } from "@/api/client";
import type { components } from "@/api/schema";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDeadline, stateTone, urgency } from "@/lib/format";
import { cn } from "@/lib/utils";

type BidSummary = components["schemas"]["BidSummaryOut"];

function useBids() {
  return useQuery({
    queryKey: ["bids"],
    queryFn: async () => {
      const { data, error } = await api.GET("/bids");
      if (error || !data) throw new Error("Could not load your bids");
      return data;
    },
  });
}

function Stat({
  icon,
  label,
  value,
  tone,
}: {
  icon: ReactNode;
  label: string;
  value: number;
  tone?: "warn" | "bad";
}) {
  return (
    <div className="fb-card flex items-center gap-4 p-4">
      <span
        className={cn(
          "grid size-10 place-items-center rounded-lg bg-accent text-accent-foreground",
          tone === "warn" && value > 0 && "bg-amber-50 text-amber-700",
          tone === "bad" && value > 0 && "bg-red-50 text-red-700",
        )}
      >
        {icon}
      </span>
      <div>
        <div className="text-2xl leading-tight font-semibold tabular-nums">{value}</div>
        <div className="text-xs text-muted-foreground">{label}</div>
      </div>
    </div>
  );
}

function BidRow({ bid }: { bid: BidSummary }) {
  return (
    <tr className="border-b last:border-0">
      <td className="py-3 pr-4 align-top">
        <Link to={`/bids/${bid.id}`} className="font-semibold">
          {bid.human_id}
        </Link>
        <div className="text-foreground">{bid.client_name}</div>
        <div className="text-xs text-muted-foreground">{bid.tender_reference}</div>
      </td>
      <td className="py-3 pr-4 align-top">
        <Badge tone={stateTone(bid.state)}>{bid.state.replaceAll("_", " ")}</Badge>
        <div className="mt-1 text-xs text-muted-foreground">Stage {bid.stage}</div>
      </td>
      <td className={cn("py-3 pr-4 align-top", urgency(bid.days_to_submission))}>
        {formatDeadline(bid.submission_deadline, bid.days_to_submission)}
      </td>
      <td className={cn("py-3 pr-4 align-top", urgency(bid.days_to_clarification_cutoff))}>
        {formatDeadline(bid.clarification_cutoff, bid.days_to_clarification_cutoff)}
      </td>
      <td className="py-3 pr-4 align-top">
        {bid.open_tasks} open
        {bid.overdue_tasks > 0 && (
          <span className="font-medium text-destructive"> · {bid.overdue_tasks} overdue</span>
        )}
      </td>
      <td className="py-3 align-top">
        {bid.missing_mandatory_fields.length > 0 ? (
          <span className="text-amber-700 dark:text-amber-400">
            Missing: {bid.missing_mandatory_fields.join(", ").replaceAll("_", " ")}
          </span>
        ) : (
          <span className="text-muted-foreground">Complete</span>
        )}
      </td>
    </tr>
  );
}

export function DashboardPage() {
  const bids = useBids();
  const rows = bids.data ?? [];
  const dueSoon = rows.filter(
    (bid) => bid.days_to_submission !== null && bid.days_to_submission <= 7,
  ).length;

  return (
    <section className="space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Your bids</h1>
          <p className="text-sm text-muted-foreground">
            Only the bids you are on. Deadlines nearest first.
          </p>
        </div>
        <Button asChild>
          <Link to="/bids/new">
            <Plus aria-hidden />
            New bid
          </Link>
        </Button>
      </div>

      {bids.isPending && (
        <p role="status" className="text-sm text-muted-foreground">
          Loading your bids…
        </p>
      )}
      {bids.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load your bids
        </p>
      )}
      {bids.isSuccess &&
        (rows.length === 0 ? (
          <div className="fb-card grid place-items-center gap-2 px-6 py-16 text-center">
            <FolderOpen className="size-8 text-muted-foreground" aria-hidden />
            <p className="text-sm text-muted-foreground">
              You are not on any bids yet. Register one to get started.
            </p>
          </div>
        ) : (
          <>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Stat icon={<FolderOpen className="size-5" aria-hidden />} label="Active bids" value={rows.length} />
              <Stat
                icon={<CalendarClock className="size-5" aria-hidden />}
                label="Due within 7 days"
                value={dueSoon}
                tone="warn"
              />
              <Stat
                icon={<ListTodo className="size-5" aria-hidden />}
                label="Tasks to do"
                value={rows.reduce((sum, bid) => sum + bid.open_tasks, 0)}
              />
              <Stat
                icon={<TriangleAlert className="size-5" aria-hidden />}
                label="Tasks past due"
                value={rows.reduce((sum, bid) => sum + bid.overdue_tasks, 0)}
                tone="bad"
              />
            </div>
            <div className="fb-card overflow-hidden">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">Bids you are working on</caption>
                <thead>
                  <tr>
                    <th scope="col" className="py-2.5 pr-4">
                      Bid
                    </th>
                    <th scope="col" className="py-2.5 pr-4">
                      State
                    </th>
                    <th scope="col" className="py-2.5 pr-4">
                      Submission
                    </th>
                    <th scope="col" className="py-2.5 pr-4">
                      Clarifications close
                    </th>
                    <th scope="col" className="py-2.5 pr-4">
                      Tasks
                    </th>
                    <th scope="col" className="py-2.5">
                      Details
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((bid) => (
                    <BidRow key={bid.id} bid={bid} />
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ))}
    </section>
  );
}
