import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/**
 * Proposed changes to the rate and productivity libraries (FR-LRN-03).
 *
 * Anyone may propose a change, with the reason. Nothing in a library changes until the
 * senior estimator approves the proposal; approval applies it as a new version.
 */

type Proposal = components["schemas"]["ProposalOut"];

const EMPTY = {
  item_key: "",
  unit: "",
  unit_rate: "",
  source_type: "purchase_order",
  source_reference: "",
  description: "",
  effective_from: "",
};

function summary(row: Proposal): string {
  const p = row.payload as Record<string, string | null>;
  return row.library === "rate"
    ? `${p.description} · ${p.item_key} · SGD ${p.unit_rate} per ${p.unit} · ${p.source_type?.replaceAll("_", " ")}: ${p.source_reference}`
    : `${p.description} · ${p.item_type}${p.dn ? ` DN${p.dn}` : ""} · ${p.hours_per_unit} h per ${p.unit} · ${p.source_type?.replaceAll("_", " ")}: ${p.source_reference}`;
}

export function LibraryProposals({ canDecide }: { canDecide: boolean }) {
  const client = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const fail = (caught: unknown) =>
    setError(caught instanceof Error ? caught.message : "Something went wrong");
  const done = () => {
    setError(null);
    void client.invalidateQueries({ queryKey: ["library-proposals"] });
    void client.invalidateQueries({ queryKey: ["rates"] });
  };
  const proposals = useQuery({
    queryKey: ["library-proposals"],
    queryFn: async (): Promise<Proposal[]> => {
      const { data } = await api.GET("/library-proposals");
      return data ?? [];
    },
  });
  const propose = useMutation({
    mutationFn: async () => {
      const { error: refused } = await api.POST("/library-proposals", {
        body: { library: "rate", payload: form, source: "estimator", reason },
      });
      if (refused) throw new Error(apiErrorMessage(refused, "The proposal was not accepted"));
    },
    onSuccess: () => {
      setForm(EMPTY);
      setReason("");
      done();
    },
    onError: fail,
  });
  const decide = useMutation({
    mutationFn: async (input: { id: string; approve: boolean }) => {
      const { error: refused } = await api.POST("/library-proposals/{proposal_id}/decide", {
        params: { path: { proposal_id: input.id } },
        body: { approve: input.approve, note: note || null },
      });
      if (refused) throw new Error(apiErrorMessage(refused, "The decision was not recorded"));
    },
    onSuccess: () => {
      setNote("");
      done();
    },
    onError: fail,
  });
  const field = (name: keyof typeof EMPTY, label: string, width = "w-40") => (
    <input
      aria-label={label}
      className={width}
      placeholder={label}
      value={form[name]}
      onChange={(event) => setForm({ ...form, [name]: event.target.value })}
    />
  );
  const rows = proposals.data ?? [];
  const waiting = rows.filter((row) => row.state === "proposed");

  return (
    <div className="space-y-3">
      <h2 className="text-lg font-medium">
        Proposed changes{" "}
        <span className="text-sm font-normal text-muted-foreground">
          a library changes only when the senior estimator approves a proposal
        </span>
      </h2>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing is proposed.</p>
      ) : (
        <ul aria-label="Library proposals" className="divide-y rounded-lg border text-sm">
          {rows.map((row) => (
            <li key={row.id} className="flex flex-wrap items-start justify-between gap-3 p-3">
              <div className="min-w-0 flex-1">
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  <Badge tone="info">{row.library}</Badge>
                  <Badge
                    tone={row.state === "approved" ? "good" : row.state === "rejected" ? "bad" : "warn"}
                  >
                    {row.state}
                    {row.decided_by ? ` by ${row.decided_by}` : ""}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    from {row.source}
                    {row.source_ref ? ` ${row.source_ref}` : ""}, by {row.proposed_by}
                  </span>
                </div>
                <div>{summary(row)}</div>
                <div className="text-xs text-muted-foreground">Why: {row.reason}</div>
                {row.note && <div className="text-xs text-muted-foreground">Note: {row.note}</div>}
              </div>
              {canDecide && row.state === "proposed" && (
                <div className="flex shrink-0 gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={decide.isPending}
                    onClick={() => decide.mutate({ id: row.id, approve: true })}
                  >
                    Approve
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={decide.isPending || !note.trim()}
                    onClick={() => decide.mutate({ id: row.id, approve: false })}
                  >
                    Reject
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {canDecide && waiting.length > 0 && (
        <input
          aria-label="Decision note"
          className="w-96"
          placeholder="Note (needed to reject)"
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      )}
      <form
        aria-label="Propose a rate"
        className="flex flex-wrap items-center gap-2 rounded-lg border p-3 text-sm"
        onSubmit={(event) => {
          event.preventDefault();
          propose.mutate();
        }}
      >
        <span className="w-full font-medium">Propose a rate</span>
        {field("item_key", "Item key: type|dn|material|…", "w-64")}
        {field("description", "Description", "w-64")}
        {field("unit", "Unit", "w-20")}
        {field("unit_rate", "Rate (SGD)", "w-28")}
        <select
          aria-label="Source type"
          value={form.source_type}
          onChange={(event) => setForm({ ...form, source_type: event.target.value })}
        >
          <option value="purchase_order">Purchase order</option>
          <option value="quotation">Quotation</option>
          <option value="company_standard">Company standard</option>
        </select>
        {field("source_reference", "Source reference")}
        <input
          type="date"
          aria-label="Effective from"
          value={form.effective_from}
          onChange={(event) => setForm({ ...form, effective_from: event.target.value })}
        />
        <input
          aria-label="Why the library should change"
          className="w-80"
          placeholder="Why the library should change"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
        <Button type="submit" variant="outline" disabled={propose.isPending || !reason.trim()}>
          Propose
        </Button>
      </form>
    </div>
  );
}
