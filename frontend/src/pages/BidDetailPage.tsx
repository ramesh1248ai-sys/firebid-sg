import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ChevronRight,
  ClipboardList,
  FileQuestion,
  FileStack,
  Pencil,
  PencilRuler,
  Receipt,
  ScrollText,
  ShieldAlert,
  Shapes,
  SquareMousePointer,
  TriangleAlert,
} from "lucide-react";
import { type ReactNode, useState } from "react";
import { Link, useParams } from "react-router";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { asInstant, formatDeadline, stateTone } from "@/lib/format";

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

function Detail({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="px-5 py-4">
      <dt className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">
        {label}
      </dt>
      <dd className="mt-1 text-sm font-medium">{value}</dd>
    </div>
  );
}

/** An instant as a `datetime-local` input shows it: the wall-clock time here. */
function asLocal(iso: string | null): string {
  if (!iso) return "";
  const when = new Date(iso);
  const pad = (part: number) => String(part).padStart(2, "0");
  return (
    `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}` +
    `T${pad(when.getHours())}:${pad(when.getMinutes())}`
  );
}

/**
 * A detail a bid may be registered without and given later: shown as the others are, with a
 * way to change it in place. Qualification waits on these, so they are set where the page
 * says they are missing.
 */
function EditableDetail({
  label,
  shown,
  initial,
  input,
  frozen,
  onSave,
}: {
  label: string;
  shown: string;
  initial: string;
  input: { type: "datetime-local" } | { type: "number"; min: number; max: number; unit: string };
  frozen: boolean;
  onSave: (value: string) => Promise<unknown>;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const id = `bid-${label.toLowerCase().replaceAll(" ", "-")}`;

  if (!editing) {
    return (
      <div className="px-5 py-4">
        <dt className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">
          {label}
        </dt>
        <dd className="mt-1 flex items-center gap-2 text-sm font-medium">
          <span className={shown === "not set" ? "text-amber-700 dark:text-amber-400" : ""}>
            {shown}
          </span>
          {!frozen && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-link"
              aria-label={`${shown === "not set" ? "Set" : "Change"} ${label.toLowerCase()}`}
              onClick={() => {
                setValue(initial);
                setError(null);
                setEditing(true);
              }}
            >
              <Pencil aria-hidden />
              {shown === "not set" ? "Set" : "Change"}
            </Button>
          )}
        </dd>
      </div>
    );
  }
  return (
    <div className="px-5 py-4">
      <dt className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">
        <label htmlFor={id}>{label}</label>
      </dt>
      <dd className="mt-1">
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            setSaving(true);
            setError(null);
            onSave(value)
              .then(() => setEditing(false))
              .catch((caught: unknown) =>
                setError(caught instanceof Error ? caught.message : "Could not save"),
              )
              .finally(() => setSaving(false));
          }}
        >
          <input
            id={id}
            autoFocus
            required
            type={input.type}
            {...(input.type === "number" ? { min: input.min, max: input.max, step: 1 } : {})}
            className={input.type === "number" ? "w-24" : "w-56"}
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
          {input.type === "number" && (
            <span className="text-sm text-muted-foreground">{input.unit}</span>
          )}
          <Button type="submit" size="sm" disabled={saving || !value}>
            Save
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={() => setEditing(false)}>
            Cancel
          </Button>
        </form>
        {error && (
          <p role="alert" className="mt-1 text-xs text-destructive">
            {error}
          </p>
        )}
      </dd>
    </div>
  );
}

// A bid's parts, in the order the work goes through them.
const PARTS: { to: string; label: string; what: string; icon: ReactNode }[] = [
  {
    to: "documents",
    label: "Tender documents",
    what: "Upload drawings, specifications and schedules, and open the sheets.",
    icon: <FileStack className="size-5" aria-hidden />,
  },
  {
    to: "registers",
    label: "Registers",
    what: "The drawing and document registers, with revisions and what is current.",
    icon: <ClipboardList className="size-5" aria-hidden />,
  },
  {
    to: "symbols",
    label: "Symbols",
    what: "Confirm what each legend symbol is before anything is counted.",
    icon: <Shapes className="size-5" aria-hidden />,
  },
  {
    to: "specification",
    label: "Specification",
    what: "Attributes, obligations, issues with the drawings and the scope matrix.",
    icon: <ScrollText className="size-5" aria-hidden />,
  },
  {
    to: "design",
    label: "Design development",
    what: "Propose a layout to take off from where drawings show design intent only.",
    icon: <PencilRuler className="size-5" aria-hidden />,
  },
  {
    to: "workbench",
    label: "Workbench",
    what: "Review the takeoff against the drawings, settle duplicates and approve G1.",
    icon: <SquareMousePointer className="size-5" aria-hidden />,
  },
  {
    to: "boq",
    label: "BOQ",
    what: "The bill of quantities, pricing, quotations, labour and the cost build-up.",
    icon: <Receipt className="size-5" aria-hidden />,
  },
  {
    to: "clarifications",
    label: "Clarifications",
    what: "Questions for the client, with their evidence, and what stays unresolved.",
    icon: <FileQuestion className="size-5" aria-hidden />,
  },
  {
    to: "risk",
    label: "Risk and qualifications",
    what: "The scope-gap checklist, the risk register and what the offer is qualified by.",
    icon: <ShieldAlert className="size-5" aria-hidden />,
  },
];

export function BidDetailPage() {
  const { bidId = "" } = useParams();
  const bid = useBid(bidId);
  const client = useQueryClient();
  const update = useMutation({
    mutationFn: async (body: components["schemas"]["BidUpdate"]) => {
      const { data, error } = await api.PATCH("/bids/{bid_id}", {
        params: { path: { bid_id: bidId } },
        body,
      });
      if (error || !data) throw new Error(apiErrorMessage(error, "Could not save the change"));
      return data;
    },
    onSuccess: (data) => {
      client.setQueryData(["bid", bidId], data);
      void client.invalidateQueries({ queryKey: ["bids"] });
    },
  });

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
      <section className="fb-card max-w-xl space-y-3 p-8">
        <h1 className="text-2xl font-semibold tracking-tight">
          {missing ? "Bid not found" : "Could not load this bid"}
        </h1>
        <p role="alert" className="text-sm text-muted-foreground">
          {missing
            ? "It may not exist, or you may not be on its team."
            : "Please try again in a moment."}
        </p>
        <Link to="/" className="inline-flex items-center gap-1.5 text-sm font-medium">
          <ArrowLeft className="size-4" aria-hidden />
          Back to your bids
        </Link>
      </section>
    );
  }

  return (
    <section className="space-y-6">
      <div className="fb-card overflow-hidden">
        <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-5">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">{bid.data.human_id}</h1>
            <p className="mt-0.5 text-sm text-muted-foreground">
              {bid.data.client_name} · {bid.data.tender_reference}
              {bid.data.consultant ? ` · drawings by ${bid.data.consultant}` : ""}
            </p>
          </div>
          <Badge tone={stateTone(bid.data.state)} className="mt-1 text-sm">
            {bid.data.state.replaceAll("_", " ")}
          </Badge>
        </div>
        <dl className="grid divide-x divide-y border-t bg-muted/40 sm:grid-cols-2 lg:grid-cols-4 lg:divide-y-0">
          <Detail label="Stage" value={bid.data.stage} />
          <Detail label="Submission" value={formatDeadline(bid.data.submission_deadline, null)} />
          <EditableDetail
            label="Clarifications close"
            shown={formatDeadline(bid.data.clarification_cutoff, null)}
            initial={asLocal(bid.data.clarification_cutoff)}
            input={{ type: "datetime-local" }}
            // A submitted bid is frozen: the API refuses a change to it.
            frozen={bid.data.state === "submitted"}
            onSave={(value) => update.mutateAsync({ clarification_cutoff: asInstant(value) })}
          />
          <EditableDetail
            label="Tender validity"
            shown={
              bid.data.tender_validity_days ? `${bid.data.tender_validity_days} days` : "not set"
            }
            initial={bid.data.tender_validity_days ? String(bid.data.tender_validity_days) : ""}
            input={{ type: "number", min: 1, max: 365, unit: "days" }}
            frozen={bid.data.state === "submitted"}
            onSave={(value) => update.mutateAsync({ tender_validity_days: Number(value) })}
          />
        </dl>
      </div>

      {bid.data.missing_mandatory_fields.length > 0 && (
        <p
          role="status"
          className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200"
        >
          <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>
            Qualification is blocked until these are in:{" "}
            {bid.data.missing_mandatory_fields.join(", ").replaceAll("_", " ")}
          </span>
        </p>
      )}

      <nav aria-label="Parts of the bid" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {PARTS.map((part, index) => (
          <div
            key={part.to}
            className="fb-card group relative flex items-start gap-4 p-5 transition-shadow hover:shadow-md"
          >
            <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-accent text-accent-foreground">
              {part.icon}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-semibold text-muted-foreground tabular-nums">
                  {String(index + 1).padStart(2, "0")}
                </span>
                {/* The link's name is the part's alone; the whole card is its target. */}
                <Link
                  to={`/bids/${bidId}/${part.to}`}
                  className="font-semibold text-foreground after:absolute after:inset-0 hover:no-underline"
                >
                  {part.label}
                </Link>
              </div>
              <p className="mt-1 text-sm text-muted-foreground">{part.what}</p>
            </div>
            <ChevronRight
              className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
              aria-hidden
            />
          </div>
        ))}
      </nav>
    </section>
  );
}
