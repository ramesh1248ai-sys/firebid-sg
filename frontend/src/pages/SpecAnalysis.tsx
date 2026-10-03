import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { accessToken } from "@/auth/oidc";
import { Button } from "@/components/ui/button";

/**
 * The rest of the specification (FR-SPEC-02, 03, 04): what it obliges the contractor to do,
 * where it and the drawings disagree, and whose each obligation and interface is.
 *
 * Every row is one click from the clause it cites, and an issue from the sheet it cites
 * too. Statuses and obligations read by rule are proposals: a person confirms them.
 */

type Obligation = components["schemas"]["ObligationOut"];
type Issue = components["schemas"]["IssueOut"];
type ScopeRow = components["schemas"]["ScopeRowOut"];

/** What the clause panel needs to open a clause with its cited words marked. */
export interface Cited {
  clause_id: string | null;
  clause_number: string;
  quote: string;
  document_title: string | null;
  revision_label: string | null;
}

type Tab = "obligations" | "issues" | "matrix";

const STATUS: Record<string, string> = {
  included: "Included",
  excluded: "Excluded",
  by_others: "By others",
  unclear: "Unclear",
};
const SEVERITY: Record<string, string> = {
  high: "text-red-700",
  medium: "text-amber-700",
  low: "text-muted-foreground",
};

function words(value: string): string {
  return value.replaceAll("_", " ");
}

function quantities(stated: Record<string, unknown>): string {
  return Object.entries(stated)
    .filter(([key]) => !key.endsWith("_unit") && key !== "count_of")
    .map(([key, value]) => {
      if (key === "pressure") return `${value} ${stated.pressure_unit ?? ""}`.trim();
      if (key === "count") return `${value} ${stated.count_of ?? ""}`.trim();
      const [what, unit] = [key.split("_")[0], key.split("_").slice(1).join(" ")];
      return what === "duration" || what === "period" ? `${value} ${unit}` : `${value} ${words(key)}`;
    })
    .join(", ");
}

export function SpecAnalysis({
  bidId,
  onOpenClause,
}: {
  bidId: string;
  onOpenClause: (cited: Cited) => void;
}) {
  const [tab, setTab] = useState<Tab>("obligations");
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["spec", bidId] });

  const obligations = useQuery({
    queryKey: ["spec", bidId, "obligations"],
    queryFn: async (): Promise<Obligation[]> => {
      const { data } = await api.GET("/bids/{bid_id}/spec/obligations", {
        params: { path: { bid_id: bidId } },
      });
      return Array.isArray(data) ? data : [];
    },
  });
  const issues = useQuery({
    queryKey: ["spec", bidId, "issues"],
    queryFn: async (): Promise<Issue[]> => {
      const { data } = await api.GET("/bids/{bid_id}/spec/issues", {
        params: { path: { bid_id: bidId } },
      });
      return Array.isArray(data) ? data : [];
    },
  });
  const matrix = useQuery({
    queryKey: ["spec", bidId, "matrix"],
    queryFn: async (): Promise<ScopeRow[]> => {
      const { data } = await api.GET("/bids/{bid_id}/spec/scope-matrix", {
        params: { path: { bid_id: bidId } },
      });
      return Array.isArray(data) ? data : [];
    },
  });

  const run = useMutation({
    mutationFn: async () => {
      const { error: failed } = await api.POST("/bids/{bid_id}/spec/analysis/run", {
        params: { path: { bid_id: bidId } },
      });
      if (failed) throw new Error(apiErrorMessage(failed, "Could not run the analysis"));
    },
    onSuccess: () => {
      setError(null);
      void refresh();
    },
    onError: (failed) => setError(String(failed.message)),
  });

  const open = (issues.data ?? []).filter((issue) => issue.state === "open").length;
  const cite = (clause_id: string | null | undefined, number: string, quote: string) =>
    onOpenClause({
      clause_id: clause_id ?? null,
      clause_number: number,
      quote,
      document_title: "",
      revision_label: null,
    });

  return (
    <section aria-label="Specification analysis" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <nav className="flex gap-1 text-sm" aria-label="Specification analysis">
          {(
            [
              ["obligations", `Obligations (${obligations.data?.length ?? 0})`],
              ["issues", `Issues (${open})`],
              ["matrix", "Scope matrix"],
            ] as [Tab, string][]
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              aria-pressed={tab === key}
              className={`rounded px-2 py-1 ${tab === key ? "bg-accent font-medium" : "hover:bg-accent/50"}`}
              onClick={() => setTab(key)}
            >
              {label}
            </button>
          ))}
        </nav>
        <Button size="sm" variant="outline" disabled={run.isPending} onClick={() => run.mutate()}>
          {run.isPending ? "Checking…" : "Check against the drawings"}
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}

      {tab === "obligations" && (
        <ObligationList
          bidId={bidId}
          rows={obligations.data ?? []}
          onCite={cite}
          onChanged={refresh}
          onError={setError}
        />
      )}
      {tab === "issues" && (
        <IssueList
          bidId={bidId}
          rows={issues.data ?? []}
          onCite={cite}
          onChanged={refresh}
          onError={setError}
        />
      )}
      {tab === "matrix" && (
        <Matrix
          bidId={bidId}
          rows={matrix.data ?? []}
          onCite={cite}
          onChanged={refresh}
          onError={setError}
        />
      )}
    </section>
  );
}

async function download(bidId: string) {
  const token = await accessToken();
  const response = await fetch(
    new URL(`/api/bids/${bidId}/spec/scope-matrix/export.xlsx`, window.location.origin),
    { headers: token ? { authorization: `Bearer ${token}` } : {} },
  );
  if (!response.ok) throw new Error("The matrix could not be exported");
  const disposition = response.headers.get("content-disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "scope-matrix.xlsx";
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

type Cite = (clauseId: string | null | undefined, number: string, quote: string) => void;

interface ListProps<Row> {
  bidId: string;
  rows: Row[];
  onCite: Cite;
  onChanged: () => void;
  onError: (message: string | null) => void;
}

function ObligationList({ bidId, rows, onCite, onChanged, onError }: ListProps<Obligation>) {
  const decide = useMutation({
    mutationFn: async ({ id, decision }: { id: string; decision: string }) => {
      const { error } = await api.POST("/bids/{bid_id}/spec/obligations/{obligation_id}/decide", {
        params: { path: { bid_id: bidId, obligation_id: id } },
        body: { decision },
      });
      if (error) throw new Error(apiErrorMessage(error, "Could not record the decision"));
    },
    onSuccess: () => {
      onError(null);
      onChanged();
    },
    onError: (failed) => onError(String(failed.message)),
  });
  if (rows.length === 0)
    return <p className="text-sm text-muted-foreground">No obligations have been read yet.</p>;
  return (
    <table className="w-full text-sm" aria-label="Obligations">
      <thead className="text-left text-xs text-muted-foreground">
        <tr>
          <th className="py-2 pr-3">Category</th>
          <th className="py-2 pr-3">What the specification obliges</th>
          <th className="py-2 pr-3">Stated</th>
          <th className="py-2 pr-3">Clause</th>
          <th className="py-2 pr-3">Status</th>
          <th className="py-2" />
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id} className="border-t align-top">
            <td className="py-2 pr-3 capitalize">{words(row.category)}</td>
            <td className="py-2 pr-3">
              {row.summary}
              {!row.citation_ok && (
                <span className="block text-xs text-red-700">{row.citation_reason}</span>
              )}
            </td>
            <td className="py-2 pr-3">{quantities(row.quantities)}</td>
            <td className="py-2 pr-3">
              <button
                type="button"
                className="underline"
                onClick={() => onCite(row.clause_id, row.clause_number, row.quote)}
              >
                {row.clause_number}
              </button>
            </td>
            <td className="py-2 pr-3">
              {row.state === "proposed"
                ? `to confirm · ${row.method}`
                : `${row.state} by ${row.decided_by}`}
            </td>
            <td className="py-2 text-right">
              {row.state === "proposed" && (
                <span className="flex justify-end gap-1">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => decide.mutate({ id: row.id, decision: "confirm" })}
                  >
                    Confirm
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => decide.mutate({ id: row.id, decision: "reject" })}
                  >
                    Reject
                  </Button>
                </span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function IssueList({ bidId, rows, onCite, onChanged, onError }: ListProps<Issue>) {
  const dismiss = useMutation({
    mutationFn: async ({ id, note }: { id: string; note: string }) => {
      const { error } = await api.POST("/bids/{bid_id}/spec/issues/{issue_id}/decide", {
        params: { path: { bid_id: bidId, issue_id: id } },
        body: { decision: "dismiss", note },
      });
      if (error) throw new Error(apiErrorMessage(error, "Could not dismiss the issue"));
    },
    onSuccess: () => {
      onError(null);
      onChanged();
    },
    onError: (failed) => onError(String(failed.message)),
  });
  const [reasons, setReasons] = useState<Record<string, string>>({});
  if (rows.length === 0)
    return (
      <p className="text-sm text-muted-foreground">
        No issues yet. Check the specification against the drawings once both are read.
      </p>
    );
  return (
    <ul aria-label="Issues" className="divide-y rounded border text-sm">
      {rows.map((row) => {
        const clause = row.spec_ref.clause as string | null | undefined;
        const sheet = row.drawing_ref.sheet_number as string | null | undefined;
        const sheetId = row.drawing_ref.sheet_id as string | undefined;
        const looked = (row.drawing_ref.sheets as { sheet_number: string }[] | undefined) ?? [];
        return (
          <li key={row.id} className="space-y-1 p-2">
            <div className="flex items-baseline gap-2">
              <span className={`w-16 shrink-0 text-xs uppercase ${SEVERITY[row.severity] ?? ""}`}>
                {row.severity}
              </span>
              <span className="font-medium">{row.title}</span>
              <span className="ml-auto text-xs text-muted-foreground">
                {row.category}
                {row.state !== "open" ? ` · ${row.state}` : ""}
              </span>
            </div>
            <div className="pl-[4.5rem] text-xs">
              Specification:{" "}
              {clause ? (
                <button
                  type="button"
                  className="underline"
                  onClick={() =>
                    onCite(
                      row.spec_ref.clause_id as string | undefined,
                      clause,
                      String(row.spec_ref.quote ?? ""),
                    )
                  }
                >
                  clause {clause}
                </button>
              ) : (
                <span>{String(row.spec_ref.note ?? "no clause")}</span>
              )}{" "}
              (revision {String(row.spec_ref.revision ?? "")}) · Drawing:{" "}
              {sheet && sheetId ? (
                <Link className="underline" to={`/bids/${bidId}/sheets/${sheetId}`}>
                  {sheet} {String(row.drawing_ref.revision ?? "")}
                </Link>
              ) : (
                <span>
                  {String(row.drawing_ref.note ?? "")} ({looked.map((s) => s.sheet_number).join(", ")})
                </span>
              )}
              {typeof row.drawing_ref.note === "string" && sheet && (
                <span className="block text-muted-foreground">“{row.drawing_ref.note}”</span>
              )}
            </div>
            {row.state === "open" && (
              <form
                className="flex gap-1 pl-[4.5rem]"
                onSubmit={(event) => {
                  event.preventDefault();
                  dismiss.mutate({ id: row.id, note: reasons[row.id] ?? "" });
                }}
              >
                <input
                  aria-label={`Why ${row.title} is not an issue`}
                  className="flex-1 rounded border px-2 py-1 text-xs"
                  placeholder="Not an issue because…"
                  value={reasons[row.id] ?? ""}
                  onChange={(event) => setReasons({ ...reasons, [row.id]: event.target.value })}
                />
                <Button size="sm" variant="ghost" type="submit" disabled={!reasons[row.id]}>
                  Dismiss
                </Button>
              </form>
            )}
            {row.state === "dismissed" && (
              <p className="pl-[4.5rem] text-xs text-muted-foreground">
                Dismissed by {row.decided_by}: {row.note}
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function Matrix({ bidId, rows, onCite, onChanged, onError }: ListProps<ScopeRow>) {
  const done = {
    onSuccess: () => {
      onError(null);
      onChanged();
    },
    onError: (failed: Error) => onError(String(failed.message)),
  };
  const edit = useMutation({
    mutationFn: async ({ id, status }: { id: string; status: string }) => {
      const { error } = await api.POST("/bids/{bid_id}/spec/scope-matrix/rows/{row_id}", {
        params: { path: { bid_id: bidId, row_id: id } },
        body: { status },
      });
      if (error) throw new Error(apiErrorMessage(error, "Could not change the row"));
    },
    ...done,
  });
  const confirm = useMutation({
    mutationFn: async () => {
      const { error } = await api.POST("/bids/{bid_id}/spec/scope-matrix/confirm", {
        params: { path: { bid_id: bidId } },
      });
      if (error) throw new Error(apiErrorMessage(error, "Could not confirm the matrix"));
    },
    ...done,
  });
  if (rows.length === 0)
    return (
      <p className="text-sm text-muted-foreground">
        No scope matrix yet. Check the specification against the drawings to build it.
      </p>
    );
  const systems = [...new Set(rows.map((row) => row.system))];
  const confirmed = rows.every((row) => row.confirmed_by);
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm">
        <span>
          {confirmed
            ? `Confirmed by ${rows[0]!.confirmed_by}`
            : `${rows.filter((row) => row.status === "unclear").length} unclear · not confirmed`}
        </span>
        <Button size="sm" variant="outline" disabled={confirm.isPending} onClick={() => confirm.mutate()}>
          Confirm the matrix
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => download(bidId).catch(() => onError("The matrix could not be exported"))}
        >
          Export to Excel
        </Button>
      </div>
      {systems.map((system) => (
        <table key={system} className="w-full text-sm" aria-label={`Scope of ${words(system)}`}>
          <caption className="py-1 text-left font-medium capitalize">{words(system)}</caption>
          <thead className="text-left text-xs text-muted-foreground">
            <tr>
              <th className="py-1 pr-3">Item</th>
              <th className="py-1 pr-3">Status</th>
              <th className="py-1 pr-3">Clause</th>
              <th className="py-1">Basis</th>
            </tr>
          </thead>
          <tbody>
            {rows
              .filter((row) => row.system === system)
              .map((row) => (
                <tr key={row.id} className="border-t">
                  <td className="py-1 pr-3">{row.label}</td>
                  <td className="py-1 pr-3">
                    <select
                      aria-label={`Status of ${row.label} for ${words(system)}`}
                      className="rounded border px-1 py-0.5"
                      value={row.status}
                      onChange={(event) => edit.mutate({ id: row.id, status: event.target.value })}
                    >
                      {Object.entries(STATUS).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="py-1 pr-3">
                    {row.clause_number ? (
                      <button
                        type="button"
                        className="underline"
                        onClick={() => onCite(row.clause_id, row.clause_number!, row.quote ?? "")}
                      >
                        {row.clause_number}
                      </button>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="py-1 text-xs text-muted-foreground">
                    {row.source === "person" ? `set by ${row.edited_by}` : row.reason}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      ))}
    </div>
  );
}
