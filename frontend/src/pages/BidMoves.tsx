import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";

/**
 * Moving a bid on (FR-BID-01, requirements §7).
 *
 * The bid's state is moved by a named person, each move by the role it belongs to: the bid
 * manager starts qualification and submits for review; the Commercial Director decides to
 * bid. A move that is not the reader's, or that something stands in the way of, is shown
 * with why, and not offered. G3, G4 and the outcome are not here: each is made where it is
 * approved or recorded, on the review page.
 */

type Bid = components["schemas"]["BidOut"];
type Move = components["schemas"]["MoveOut"];
type Target = components["schemas"]["TransitionIn"]["target"];

// A move that ends the bid is not made without saying why.
const NEEDS_A_REASON = ["withdrawn", "no_bid"];
const words = (text: string) => text.replaceAll("_", " ");
const title = (action: string) => action.charAt(0).toUpperCase() + action.slice(1);

export function BidMoves({ bidId, state }: { bidId: string; state: string }) {
  const client = useQueryClient();
  const path = { params: { path: { bid_id: bidId } } };
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const moves = useQuery({
    // Asked again whenever the state changes: the moves are the state's.
    queryKey: ["bid", bidId, "moves", state],
    queryFn: async (): Promise<Move[]> => {
      const { data } = await api.GET("/bids/{bid_id}/transitions", path);
      if (!Array.isArray(data)) throw new Error("Could not read the bid's moves");
      return data;
    },
  });
  const move = useMutation({
    mutationFn: async (target: string): Promise<Bid> => {
      const { data, error: refused } = await api.POST("/bids/{bid_id}/transitions", {
        ...path,
        body: { target: target as Target, reason: reason.trim() || null },
      });
      if (refused || !data) throw new Error(apiErrorMessage(refused, "The bid was not moved"));
      return data;
    },
    onSuccess: (data) => {
      setReason("");
      setError(null);
      client.setQueryData(["bid", bidId], data);
      void client.invalidateQueries({ queryKey: ["bids"] });
      void client.invalidateQueries({ queryKey: ["review", bidId] });
    },
    onError: (caught) =>
      setError(caught instanceof Error ? caught.message : "The bid was not moved"),
  });

  if (!moves.data || moves.data.length === 0) return null;
  return (
    <section aria-label="Move the bid on" className="fb-card space-y-3 p-5">
      <div>
        <h2 className="text-lg font-medium">Move the bid on</h2>
        <p className="text-sm text-muted-foreground">
          The bid is {words(state)}. G3 and G4 are approved, and the outcome recorded, on the
          review page.
        </p>
      </div>
      <label className="block text-sm">
        <span className="block text-xs text-muted-foreground">
          Reason for the record (needed to withdraw or to decide not to bid)
        </span>
        <input
          aria-label="Reason for the move"
          className="w-full max-w-xl"
          maxLength={500}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </label>
      <ul aria-label="Moves" className="space-y-2">
        {moves.data.map((one) => {
          const wants = NEEDS_A_REASON.includes(one.target) && !reason.trim();
          const why = !one.permitted
            ? `For the ${one.roles.map(words).join(" or the ")}.`
            : one.refusal
              ? `Waits on: ${words(one.refusal)}.`
              : wants
                ? "Give a reason first."
                : null;
          return (
            <li key={one.target} className="flex flex-wrap items-center gap-3">
              <Button
                size="sm"
                variant={NEEDS_A_REASON.includes(one.target) ? "outline" : "default"}
                disabled={!one.permitted || Boolean(one.refusal) || wants || move.isPending}
                onClick={() => move.mutate(one.target)}
              >
                {title(one.action)}
              </Button>
              <span className="text-sm text-muted-foreground">
                to {words(one.target)}
                {why && <span className="ml-2 text-amber-700 dark:text-amber-400">{why}</span>}
              </span>
            </li>
          );
        })}
      </ul>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </section>
  );
}
