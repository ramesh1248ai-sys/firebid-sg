import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { api } from "@/api/client";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";
import { formatDeadline, urgency } from "@/lib/format";
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

function BidRow({ bid }: { bid: BidSummary }) {
  return (
    <tr className="border-b last:border-0">
      <td className="py-3 pr-4 align-top">
        <Link to={`/bids/${bid.id}`} className="font-medium hover:underline">
          {bid.human_id}
        </Link>
        <div className="text-muted-foreground">{bid.client_name}</div>
        <div className="text-xs text-muted-foreground">{bid.tender_reference}</div>
      </td>
      <td className="py-3 pr-4 align-top">
        <div>{bid.state.replaceAll("_", " ")}</div>
        <div className="text-xs text-muted-foreground">Stage {bid.stage}</div>
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
          <span className="text-destructive"> · {bid.overdue_tasks} overdue</span>
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

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Your bids</h1>
          <p className="text-sm text-muted-foreground">
            Only the bids you are on. Deadlines nearest first.
          </p>
        </div>
        <Button asChild>
          <Link to="/bids/new">New bid</Link>
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
        (bids.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            You are not on any bids yet. Register one to get started.
          </p>
        ) : (
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Bids you are working on</caption>
            <thead className="border-b text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th scope="col" className="py-2 pr-4 font-medium">
                  Bid
                </th>
                <th scope="col" className="py-2 pr-4 font-medium">
                  State
                </th>
                <th scope="col" className="py-2 pr-4 font-medium">
                  Submission
                </th>
                <th scope="col" className="py-2 pr-4 font-medium">
                  Clarifications close
                </th>
                <th scope="col" className="py-2 pr-4 font-medium">
                  Tasks
                </th>
                <th scope="col" className="py-2 font-medium">
                  Details
                </th>
              </tr>
            </thead>
            <tbody>
              {bids.data.map((bid) => (
                <BidRow key={bid.id} bid={bid} />
              ))}
            </tbody>
          </table>
        ))}
    </section>
  );
}
