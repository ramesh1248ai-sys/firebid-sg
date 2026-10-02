import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router";

import { api } from "@/api/client";
import { formatDeadline } from "@/lib/format";

function useBid(bidId: string) {
  return useQuery({
    queryKey: ["bid", bidId],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/bids/{bid_id}", {
        params: { path: { bid_id: bidId } },
      });
      if (response.status === 404) throw new Error("not-found");
      if (error || !data) throw new Error("Could not load this bid");
      return data;
    },
    retry: false,
  });
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd className="text-sm">{value}</dd>
    </div>
  );
}

export function BidDetailPage() {
  const { bidId = "" } = useParams();
  const bid = useBid(bidId);

  if (bid.isPending) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading the bid…
      </p>
    );
  }
  if (bid.isError) {
    const missing = bid.error.message === "not-found";
    return (
      <section className="space-y-3">
        <h1 className="text-2xl font-semibold tracking-tight">
          {missing ? "Bid not found" : "Could not load this bid"}
        </h1>
        <p role="alert" className="text-sm text-muted-foreground">
          {missing
            ? "It may not exist, or you may not be on its team."
            : "Please try again in a moment."}
        </p>
        <Link to="/" className="text-sm underline">
          Back to your bids
        </Link>
      </section>
    );
  }

  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          {bid.data.human_id}
        </h1>
        <p className="text-sm text-muted-foreground">
          {bid.data.client_name} · {bid.data.tender_reference}
          {bid.data.consultant ? ` · drawings by ${bid.data.consultant}` : ""}
        </p>
      </div>
      <Link
        to={`/bids/${bidId}/documents`}
        className="inline-block rounded-md border px-3 py-2 text-sm hover:bg-accent"
      >
        Tender documents
      </Link>{" "}
      <Link
        to={`/bids/${bidId}/registers`}
        className="inline-block rounded-md border px-3 py-2 text-sm hover:bg-accent"
      >
        Registers
      </Link>{" "}
      <Link
        to={`/bids/${bidId}/symbols`}
        className="inline-block rounded-md border px-3 py-2 text-sm hover:bg-accent"
      >
        Symbols
      </Link>{" "}
      <Link
        to={`/bids/${bidId}/specification`}
        className="inline-block rounded-md border px-3 py-2 text-sm hover:bg-accent"
      >
        Specification
      </Link>{" "}
      <Link
        to={`/bids/${bidId}/design`}
        className="inline-block rounded-md border px-3 py-2 text-sm hover:bg-accent"
      >
        Design development
      </Link>{" "}
      <Link
        to={`/bids/${bidId}/workbench`}
        className="inline-block rounded-md border px-3 py-2 text-sm hover:bg-accent"
      >
        Workbench
      </Link>{" "}
      <Link
        to={`/bids/${bidId}/boq`}
        className="inline-block rounded-md border px-3 py-2 text-sm hover:bg-accent"
      >
        BOQ
      </Link>
      <dl className="grid gap-4 sm:grid-cols-3">
        <Detail label="State" value={bid.data.state.replaceAll("_", " ")} />
        <Detail label="Stage" value={bid.data.stage} />
        <Detail
          label="Submission"
          value={formatDeadline(bid.data.submission_deadline, null)}
        />
        <Detail
          label="Clarifications close"
          value={formatDeadline(bid.data.clarification_cutoff, null)}
        />
        <Detail
          label="Tender validity"
          value={
            bid.data.tender_validity_days
              ? `${bid.data.tender_validity_days} days`
              : "not set"
          }
        />
      </dl>
      {bid.data.missing_mandatory_fields.length > 0 && (
        <p role="status" className="text-sm text-amber-700 dark:text-amber-400">
          Qualification is blocked until these are in:{" "}
          {bid.data.missing_mandatory_fields.join(", ").replaceAll("_", " ")}
        </p>
      )}
    </section>
  );
}
