import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";

import { type Cited, SpecAnalysis } from "./SpecAnalysis";

/**
 * The specification attributes takeoff will use (FR-SPEC-01, FR-SPEC-05).
 *
 * Each row is what the specification says for one system over a size range, with the
 * clause it comes from. The clause is one click away, with the cited words marked, so a
 * person can check the reading before confirming it. Rows whose citation did not hold up
 * come first: those are the ones that need a person most. Takeoff reads only confirmed rows.
 */

type Attribute = components["schemas"]["SpecAttributeOut"];
type Clause = components["schemas"]["ClauseOut"];

const SYSTEMS: Record<string, string> = {
  sprinkler: "Sprinkler",
  hose_reel: "Hose reel",
  hydrant: "Hydrant",
  wet_riser: "Wet riser",
  dry_riser: "Dry riser",
  fire_pump: "Fire pump",
  fire_protection: "Fire protection",
};

function label(value: string): string {
  return value.replaceAll("_", " ");
}

function sizes(row: Attribute): string {
  if (row.dn_min == null && row.dn_max == null) return "all sizes";
  if (row.dn_min == null) return `up to DN ${row.dn_max}`;
  if (row.dn_max == null) return `DN ${row.dn_min} and above`;
  return `DN ${row.dn_min} to ${row.dn_max}`;
}

export function SpecificationPage() {
  const { bidId = "" } = useParams();
  const [open, setOpen] = useState<Cited | null>(null);
  const rows = useQuery({
    queryKey: ["spec", bidId, "attributes"],
    queryFn: async (): Promise<Attribute[]> => {
      const { data, error } = await api.GET("/bids/{bid_id}/spec/attributes", {
        params: { path: { bid_id: bidId } },
      });
      if (error || !data)
        throw new Error("Could not read the specification attributes");
      return data;
    },
  });

  const bySystem = new Map<string, Attribute[]>();
  for (const row of rows.data ?? []) {
    bySystem.set(row.system, [...(bySystem.get(row.system) ?? []), row]);
  }

  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Specification</h1>
        <p className="text-sm text-muted-foreground">
          What the specification says takeoff needs, each with the clause it
          comes from. Takeoff uses only what a person has confirmed.
        </p>
      </div>

      {rows.isLoading && <p className="text-sm">Reading…</p>}
      {rows.error && (
        <p className="text-sm text-red-700">{String(rows.error)}</p>
      )}
      {rows.data && rows.data.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No specification has been read on this bid yet.
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
        <div className="space-y-6">
          <SpecAnalysis bidId={bidId} onOpenClause={setOpen} />
          {[...bySystem.entries()].map(([system, items]) => (
            <div key={system} className="space-y-2">
              <h2 className="text-lg font-medium">
                {SYSTEMS[system] ?? label(system)}
              </h2>
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="py-2 pr-3">Attribute</th>
                    <th className="py-2 pr-3">Value</th>
                    <th className="py-2 pr-3">Sizes</th>
                    <th className="py-2 pr-3">Clause</th>
                    <th className="py-2 pr-3">Status</th>
                    <th className="py-2" />
                  </tr>
                </thead>
                <tbody>
                  {items.map((row) => (
                    <AttributeRow
                      key={row.lineage_id}
                      bidId={bidId}
                      row={row}
                      onOpen={() => setOpen(row)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
        {open && open.clause_id && (
          <ClausePanel bidId={bidId} row={open} onClose={() => setOpen(null)} />
        )}
      </div>
    </section>
  );
}

function AttributeRow({
  bidId,
  row,
  onOpen,
}: {
  bidId: string;
  row: Attribute;
  onOpen: () => void;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(row.value);
  const decide = useMutation({
    mutationFn: async (body: {
      verdict: "confirm" | "edit" | "reject";
      value?: string;
    }) => {
      const { error } = await api.POST(
        "/bids/{bid_id}/spec/attributes/{lineage_id}/decide",
        {
          params: { path: { bid_id: bidId, lineage_id: row.lineage_id } },
          body: {
            ...body,
            dn_min: row.dn_min ?? null,
            dn_max: row.dn_max ?? null,
            condition: row.condition ?? null,
          },
        },
      );
      if (error)
        throw new Error(apiErrorMessage(error, "The decision was not saved"));
    },
    onSuccess: async () => {
      setEditing(false);
      await queryClient.invalidateQueries({ queryKey: ["spec", bidId] });
    },
  });

  return (
    <tr className="border-t align-top">
      <td className="py-2 pr-3">
        {label(row.attribute)}
        {row.condition && (
          <div className="text-xs text-muted-foreground">
            only in the {row.condition}
          </div>
        )}
      </td>
      <td className="py-2 pr-3">
        {editing ? (
          <input
            aria-label={`Value of ${label(row.attribute)}`}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            className="w-full rounded-md border bg-background px-2 py-1"
          />
        ) : (
          label(row.value)
        )}
      </td>
      <td className="py-2 pr-3">{sizes(row)}</td>
      <td className="py-2 pr-3">
        <button className="underline" onClick={onOpen}>
          {row.clause_number}
        </button>
        {!row.citation_ok && (
          <div className="text-xs text-red-700 dark:text-red-400">
            {row.citation_reason}
          </div>
        )}
      </td>
      <td className="py-2 pr-3 text-xs">
        {row.state === "verified" ? (
          <span className="text-emerald-700 dark:text-emerald-400">
            confirmed by {row.verified_by}
          </span>
        ) : row.state === "rejected" ? (
          <span className="text-red-700 dark:text-red-400">rejected</span>
        ) : (
          <span className="text-amber-700 dark:text-amber-400">
            to confirm · {row.method}
            {row.model ? ` ${row.model}` : ""} ·{" "}
            {Math.round(row.confidence * 100)}%
          </span>
        )}
      </td>
      <td className="space-x-2 py-2 text-right whitespace-nowrap">
        {row.state !== "verified" && !editing && (
          <>
            <Button
              size="sm"
              disabled={decide.isPending}
              onClick={() => decide.mutate({ verdict: "confirm" })}
            >
              Confirm
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setEditing(true)}
            >
              Edit
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={decide.isPending}
              onClick={() => decide.mutate({ verdict: "reject" })}
            >
              Reject
            </Button>
          </>
        )}
        {editing && (
          <Button
            size="sm"
            disabled={!value || decide.isPending}
            onClick={() => decide.mutate({ verdict: "edit", value })}
          >
            Save
          </Button>
        )}
        {decide.error && (
          <p className="text-xs text-red-700">{decide.error.message}</p>
        )}
      </td>
    </tr>
  );
}

function ClausePanel({
  bidId,
  row,
  onClose,
}: {
  bidId: string;
  row: Cited;
  onClose: () => void;
}) {
  const clause = useQuery({
    queryKey: ["spec", bidId, "clause", row.clause_id],
    queryFn: async (): Promise<Clause> => {
      const { data, error } = await api.GET(
        "/bids/{bid_id}/spec/clauses/{clause_id}",
        {
          params: { path: { bid_id: bidId, clause_id: row.clause_id ?? "" } },
        },
      );
      if (error || !data) throw new Error("Could not read the clause");
      return data;
    },
  });
  const body = clause.data
    ? `${clause.data.heading} ${clause.data.text}`.trim()
    : "";
  const at = row.quote ? body.indexOf(row.quote) : -1;

  return (
    <aside
      className="space-y-3 rounded-md border p-4"
      aria-label="Cited clause"
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-medium">Clause {row.clause_number}</div>
          <div className="text-xs text-muted-foreground">
            {row.document_title || clause.data?.document_title} ·{" "}
            {row.revision_label ?? clause.data?.revision_label ?? "no revision"}
            {clause.data?.anchor.page
              ? ` · page ${clause.data.anchor.page}`
              : ""}
            {clause.data?.anchor.paragraph !== undefined
              ? ` · paragraph ${clause.data.anchor.paragraph}`
              : ""}
          </div>
        </div>
        <Button size="sm" variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
      {clause.data && (
        <p className="text-sm leading-relaxed">
          {at >= 0 ? (
            <>
              {body.slice(0, at)}
              <mark>{row.quote}</mark>
              {body.slice(at + row.quote.length)}
            </>
          ) : (
            body
          )}
        </p>
      )}
    </aside>
  );
}
