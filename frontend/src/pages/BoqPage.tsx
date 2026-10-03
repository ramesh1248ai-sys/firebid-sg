import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { accessToken } from "@/auth/oidc";
import { Button } from "@/components/ui/button";
import { BoqPricing } from "@/pages/BoqPricing";
import { CostBuildUp, Quotations } from "@/pages/Costing";

/**
 * The bid's bills of quantities (P1-09).
 *
 * Our BOQ, built from the verified takeoff, every line with the QTO items behind it; the
 * client's bill, read from their own workbook; which of our lines each client line is (a
 * proposal until a person confirms it); the two reconciled, with the variances worth a
 * clarification flagged; and the exports. A line with nothing measured behind it must say
 * why (a provisional sum or a lump sum) or it holds up G1 and G2.
 */

type Boq = components["schemas"]["BoqOut"];
type Line = components["schemas"]["LineOut"];
type ClientBoq = components["schemas"]["ClientBoqOut"];
type Mapping = components["schemas"]["ClientMappingOut"];
type Row = components["schemas"]["ReconciliationOut"];
type Conventions = components["schemas"]["ConventionsOut"];
type G2 = components["schemas"]["G2Out"];

const FIELDS = ["item", "description", "unit", "quantity", "rate", "amount"];

function number(value: string | number | null | undefined): string {
  if (value == null) return "–";
  const n = Number(value);
  return Number.isInteger(n) ? String(n) : n.toFixed(2);
}

async function download(bidId: string, path: string, fallback: string) {
  const token = await accessToken();
  const response = await fetch(
    new URL(`/api/bids/${bidId}/boq/${path}`, window.location.origin),
    { headers: token ? { authorization: `Bearer ${token}` } : {} },
  );
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(apiErrorMessage(body, "The export did not work"));
  }
  const disposition = response.headers.get("content-disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? fallback;
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

function useBoqData(bidId: string) {
  const path = { params: { path: { bid_id: bidId } } };
  const boq = useQuery({
    queryKey: ["boq", bidId, "company"],
    queryFn: async (): Promise<Boq | null> => {
      const { data, response } = await api.GET("/bids/{bid_id}/boq", path);
      if (response.status === 404) return null;
      if (!data) throw new Error("Could not read the BOQ");
      return data;
    },
  });
  const client = useQuery({
    queryKey: ["boq", bidId, "client"],
    queryFn: async (): Promise<ClientBoq[]> => {
      const { data } = await api.GET("/bids/{bid_id}/boq/client", path);
      if (!data) throw new Error("Could not read the client's BOQ");
      return data;
    },
    // A bill is read on the worker after it is registered, and the model's column
    // proposal follows it.
    refetchInterval: (query) =>
      query.state.data?.some((b) => b.status === "needs_columns" && !b.proposal?.columns)
        ? 5000
        : false,
  });
  const mappings = useQuery({
    queryKey: ["boq", bidId, "mappings"],
    queryFn: async (): Promise<Mapping[]> => {
      const { data } = await api.GET("/bids/{bid_id}/boq/mappings", path);
      if (!data) throw new Error("Could not read the mappings");
      return data;
    },
    // The model's proposals arrive from the worker.
    refetchInterval: (query) =>
      query.state.data?.some((m) => m.awaiting_model) ? 5000 : false,
  });
  const reconciliation = useQuery({
    queryKey: ["boq", bidId, "reconciliation"],
    queryFn: async (): Promise<Row[]> => {
      const { data } = await api.GET("/bids/{bid_id}/boq/reconciliation", path);
      if (!data) throw new Error("Could not reconcile");
      return data;
    },
  });
  const conventions = useQuery({
    queryKey: ["boq", bidId, "conventions"],
    queryFn: async (): Promise<Conventions> => {
      const { data } = await api.GET("/bids/{bid_id}/boq/conventions", path);
      if (!data) throw new Error("Could not read the conventions");
      return data;
    },
  });
  const g2 = useQuery({
    queryKey: ["boq", bidId, "g2"],
    queryFn: async (): Promise<G2> => {
      const { data } = await api.GET("/bids/{bid_id}/boq/g2", path);
      if (!data) throw new Error("Could not read G2");
      return data;
    },
  });
  return { boq, client, mappings, reconciliation, conventions, g2 };
}

export function BoqPage() {
  const { bidId = "" } = useParams();
  const data = useBoqData(bidId);
  const client = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const path = { params: { path: { bid_id: bidId } } };
  const refresh = () => client.invalidateQueries({ queryKey: ["boq", bidId] });
  const fail = (caught: unknown) =>
    setError(caught instanceof Error ? caught.message : "That did not work");

  const build = useMutation({
    mutationFn: async () => {
      const { error: refused } = await api.POST("/bids/{bid_id}/boq/build", {
        ...path,
        body: {},
      });
      if (refused) throw new Error(apiErrorMessage(refused, "Could not build the BOQ"));
    },
    onSuccess: () => {
      setError(null);
      void refresh();
    },
    onError: fail,
  });
  const propose = useMutation({
    mutationFn: async () => {
      const { error: refused } = await api.POST(
        "/bids/{bid_id}/boq/mappings/propose",
        path,
      );
      if (refused) throw new Error(apiErrorMessage(refused, "Could not propose mappings"));
    },
    onSuccess: () => {
      setError(null);
      void refresh();
    },
    onError: fail,
  });

  const boq = data.boq.data;
  return (
    <section className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Bill of quantities</h1>
          <p className="text-sm text-muted-foreground">
            Our BOQ from the verified takeoff, the client&apos;s bill mapped to it,
            and the two reconciled.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => build.mutate()} disabled={build.isPending}>
            {boq ? "Build again from the takeoff" : "Build the BOQ"}
          </Button>
          {boq && (
            <Button
              variant="outline"
              onClick={() => download(bidId, "export.xlsx", "BOQ.xlsx").catch(fail)}
            >
              Export our BOQ
            </Button>
          )}
        </div>
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}

      <G2Status g2={data.g2.data} />

      <ClientBills bidId={bidId} bills={data.client.data ?? []} onError={fail} />

      {boq && (data.client.data ?? []).some((b) => b.status === "read") && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-medium">Which of our lines each client line is</h2>
            <Button
              variant="outline"
              onClick={() => propose.mutate()}
              disabled={propose.isPending}
            >
              Propose mappings
            </Button>
          </div>
          <MappingTable
            bidId={bidId}
            rows={data.mappings.data ?? []}
            lines={boq.lines}
            onError={fail}
          />
        </div>
      )}

      {(data.reconciliation.data ?? []).length > 0 && (
        <Reconciliation
          rows={data.reconciliation.data ?? []}
          onExport={() =>
            download(bidId, "reconciliation.xlsx", "reconciliation.xlsx").catch(fail)
          }
        />
      )}

      {boq && <BoqPricing bidId={bidId} onError={fail} />}

      {boq && <Quotations bidId={bidId} lines={boq.lines} onError={fail} />}

      {boq && <CostBuildUp bidId={bidId} onError={fail} />}

      {boq && <Lines bidId={bidId} boq={boq} onError={fail} />}

      {data.conventions.data && (
        <ConventionsPanel bidId={bidId} conventions={data.conventions.data} onError={fail} />
      )}

      <Link to={`/bids/${bidId}`} className="inline-block text-sm underline">
        Back to the bid
      </Link>
    </section>
  );
}

function G2Status({ g2 }: { g2: G2 | undefined }) {
  if (!g2) return null;
  const reasons = [
    !g2.g1_approved && "G1 is not approved",
    !g2.boq_built && "no BOQ is built",
    g2.untraced_lines.length > 0 &&
      `${g2.untraced_lines.length} line(s) with no QTO trace, not marked provisional or lump sum`,
    (g2.unsourced_lines ?? []).length > 0 &&
      `${(g2.unsourced_lines ?? []).length} priced line(s) with no source: neither a rate entry nor a named estimator's allowance`,
  ].filter(Boolean);
  return (
    <p
      role="status"
      aria-label="G2"
      className={`rounded-md border p-3 text-sm ${g2.clear ? "border-green-600" : "border-amber-500"}`}
    >
      {g2.clear ? "Nothing in the BOQ holds up G2." : `G2 is held up: ${reasons.join("; ")}.`}
    </p>
  );
}

function ClientBills({
  bidId,
  bills,
  onError,
}: {
  bidId: string;
  bills: ClientBoq[];
  onError: (caught: unknown) => void;
}) {
  if (bills.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No client BOQ has been read. Upload the client&apos;s workbook on the documents
        page; it is read once it is registered as a BOQ.
      </p>
    );
  }
  const byDocument = new Map<string, ClientBoq[]>();
  for (const bill of bills) {
    byDocument.set(bill.document_id, [...(byDocument.get(bill.document_id) ?? []), bill]);
  }
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-medium">The client&apos;s bill</h2>
      {[...byDocument.entries()].map(([documentId, sheets]) => (
        <div key={documentId} className="space-y-2 rounded-md border p-3">
          <div className="flex items-center justify-between gap-2">
            <p className="font-medium">{sheets[0]?.filename}</p>
            {sheets.every((s) => s.status === "read") && (
              <Button
                variant="outline"
                onClick={() =>
                  download(bidId, `client/${documentId}/priced.xlsx`, "priced.xlsx").catch(
                    onError,
                  )
                }
              >
                Export priced copy
              </Button>
            )}
          </div>
          {sheets.map((sheet) =>
            sheet.status === "needs_columns" ? (
              <ColumnsForm key={sheet.id} bidId={bidId} bill={sheet} onError={onError} />
            ) : (
              <p key={sheet.id} className="text-sm text-muted-foreground">
                {sheet.sheet_name}: {sheet.lines} lines read, header on row {sheet.header_row}
              </p>
            ),
          )}
        </div>
      ))}
    </div>
  );
}

type Choice = { header_row: number; columns: Record<string, number> };

type ProposedColumns = Record<
  string,
  { header_row?: number | null; columns?: Record<string, number>; reason?: string }
>;

function ColumnsForm({
  bidId,
  bill,
  onError,
}: {
  bidId: string;
  bill: ClientBoq;
  onError: (caught: unknown) => void;
}) {
  const client = useQueryClient();
  const preview = (bill.proposal?.preview ?? {}) as Record<string, string[][]>;
  const proposed = (bill.proposal?.columns ?? {}) as ProposedColumns;
  const [choices, setChoices] = useState<Record<string, Choice>>(() =>
    Object.fromEntries(
      Object.keys(preview).map((sheet) => [
        sheet,
        {
          header_row: proposed[sheet]?.header_row ?? 1,
          columns: { ...(proposed[sheet]?.columns ?? {}) },
        },
      ]),
    ),
  );
  const confirm = useMutation({
    mutationFn: async () => {
      const sheets = Object.fromEntries(
        Object.entries(choices).filter(
          ([, choice]) => choice.columns.description && choice.columns.quantity,
        ),
      );
      const { error } = await api.POST("/bids/{bid_id}/boq/client/{client_boq_id}/columns", {
        params: { path: { bid_id: bidId, client_boq_id: bill.id } },
        body: { sheets },
      });
      if (error) throw new Error(apiErrorMessage(error, "Could not read the bill"));
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["boq", bidId] }),
    onError,
  });
  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        confirm.mutate();
      }}
    >
      <p className="text-sm">
        The bill&apos;s columns could not be read with confidence ({bill.reason}). Say which
        row is the header and which column holds each field
        {Object.keys(proposed).length > 0 ? ": the model's proposal is filled in." : "."}
      </p>
      {Object.entries(preview).map(([sheet, rows]) => (
        <fieldset key={sheet} className="space-y-2 rounded border p-2">
          <legend className="px-1 text-sm font-medium">{sheet}</legend>
          <div className="max-h-48 overflow-auto">
            <table className="text-xs">
              <tbody>
                {rows.map((row, index) => (
                  <tr key={index}>
                    <td className="pr-2 text-muted-foreground">{index + 1}</td>
                    {row.map((cell, column) => (
                      <td key={column} className="border px-1">
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap gap-3 text-sm">
            <label>
              Header row{" "}
              <input
                type="number"
                min={1}
                className="w-16 rounded border px-1"
                value={choices[sheet]?.header_row ?? 1}
                onChange={(event) =>
                  setChoices((current) => ({
                    ...current,
                    [sheet]: {
                      columns: current[sheet]?.columns ?? {},
                      header_row: Number(event.target.value),
                    },
                  }))
                }
              />
            </label>
            {FIELDS.map((field) => (
              <label key={field}>
                {field}{" "}
                <input
                  type="number"
                  min={1}
                  className="w-14 rounded border px-1"
                  value={choices[sheet]?.columns[field] ?? ""}
                  onChange={(event) =>
                    setChoices((current) => {
                      const columns = { ...current[sheet]?.columns };
                      if (event.target.value) columns[field] = Number(event.target.value);
                      else delete columns[field];
                      return {
                        ...current,
                        [sheet]: { header_row: current[sheet]?.header_row ?? 1, columns },
                      };
                    })
                  }
                />
              </label>
            ))}
          </div>
        </fieldset>
      ))}
      <Button type="submit" disabled={confirm.isPending}>
        Read the bill with these columns
      </Button>
    </form>
  );
}

function MappingTable({
  bidId,
  rows,
  lines,
  onError,
}: {
  bidId: string;
  rows: Mapping[];
  lines: Line[];
  onError: (caught: unknown) => void;
}) {
  const client = useQueryClient();
  const decide = useMutation({
    mutationFn: async (input: {
      mappingId: string;
      decision: "confirm" | "correct" | "reject";
      mapsTo?: string | null;
    }) => {
      const { error } = await api.POST("/bids/{bid_id}/boq/mappings/{mapping_id}", {
        params: { path: { bid_id: bidId, mapping_id: input.mappingId } },
        body: { decision: input.decision, maps_to: input.mapsTo ?? null },
      });
      if (error) throw new Error(apiErrorMessage(error, "Could not record the decision"));
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["boq", bidId] }),
    onError,
  });
  const byKey = new Map(lines.map((line) => [line.line_key, line]));
  const measured = lines.filter((l) => !l.is_provisional && !l.is_lump_sum);
  return (
    <table className="w-full text-sm" aria-label="Mappings">
      <thead className="text-left text-xs text-muted-foreground">
        <tr>
          <th className="py-2 pr-3">Client item</th>
          <th className="py-2 pr-3">Description</th>
          <th className="py-2 pr-3">Qty</th>
          <th className="py-2 pr-3">Our line</th>
          <th className="py-2 pr-3">Proposed by</th>
          <th className="py-2" />
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const target = row.maps_to ? byKey.get(row.maps_to) : undefined;
          return (
            <tr key={row.client_line_id} className="border-t align-top">
              <td className="py-2 pr-3">{row.client_item}</td>
              <td className="py-2 pr-3">{row.description}</td>
              <td className="py-2 pr-3">
                {number(row.quantity)} {row.unit}
              </td>
              <td className="py-2 pr-3">
                {row.mapping_id ? (
                  <select
                    aria-label={`Our line for ${row.client_item}`}
                    className="max-w-72 rounded border px-1"
                    value={row.maps_to ?? ""}
                    onChange={(event) =>
                      decide.mutate({
                        mappingId: row.mapping_id!,
                        decision: "correct",
                        mapsTo: event.target.value || null,
                      })
                    }
                  >
                    <option value="">Nothing we measured</option>
                    {measured.map((line) => (
                      <option key={line.id} value={line.line_key ?? ""}>
                        {line.item_no} {line.description}
                      </option>
                    ))}
                  </select>
                ) : row.kind === "line" ? (
                  <span className="text-muted-foreground">waiting for a proposal</span>
                ) : (
                  <span className="text-muted-foreground">{row.kind.replace("_", " ")}</span>
                )}
                {target && row.variance_percent != null && (
                  <span
                    className={`ml-2 text-xs ${row.flagged ? "font-medium text-amber-700" : "text-muted-foreground"}`}
                  >
                    {row.variance_percent > 0 ? "+" : ""}
                    {row.variance_percent}%
                  </span>
                )}
              </td>
              <td className="py-2 pr-3 text-xs text-muted-foreground">
                {row.method ?? "–"}
                {row.confidence != null && row.method !== "person"
                  ? ` (${Math.round(row.confidence * 100)}%)`
                  : ""}
                {row.reason && <div>{row.reason}</div>}
              </td>
              <td className="py-2 whitespace-nowrap">
                {row.state === "proposed" && row.mapping_id && (
                  <>
                    <Button
                      variant="outline"
                      onClick={() =>
                        decide.mutate({ mappingId: row.mapping_id!, decision: "confirm" })
                      }
                    >
                      Confirm
                    </Button>{" "}
                    <Button
                      variant="outline"
                      onClick={() =>
                        decide.mutate({ mappingId: row.mapping_id!, decision: "reject" })
                      }
                    >
                      Reject
                    </Button>
                  </>
                )}
                {row.state && row.state !== "proposed" && (
                  <span className="text-xs">{row.state}</span>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Reconciliation({ rows, onExport }: { rows: Row[]; onExport: () => void }) {
  const flagged = rows.filter((r) => r.flagged).length;
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium">
          Reconciliation{" "}
          <span className="text-sm font-normal text-muted-foreground">
            {flagged} to clarify
          </span>
        </h2>
        <Button variant="outline" onClick={onExport}>
          Export the reconciliation
        </Button>
      </div>
      <table className="w-full text-sm" aria-label="Reconciliation">
        <thead className="text-left text-xs text-muted-foreground">
          <tr>
            <th className="py-2 pr-3">Client</th>
            <th className="py-2 pr-3">Client qty</th>
            <th className="py-2 pr-3">Ours</th>
            <th className="py-2 pr-3">Measured</th>
            <th className="py-2 pr-3">Variance</th>
            <th className="py-2 pr-3">QTO items</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={`${row.client_ref ?? row.line_item}-${index}`}
              className={`border-t align-top ${row.flagged ? "bg-amber-50" : ""}`}
              data-flagged={row.flagged || undefined}
            >
              <td className="py-2 pr-3">
                {row.client_item ? `${row.client_item} ${row.client_description}` : "–"}
              </td>
              <td className="py-2 pr-3">
                {row.client_quantity != null
                  ? `${number(row.client_quantity)} ${row.client_unit ?? ""}`
                  : "–"}
              </td>
              <td className="py-2 pr-3">
                {row.line_item ? `${row.line_item} ${row.line_description}` : "–"}
              </td>
              <td className="py-2 pr-3">
                {row.measured_quantity != null
                  ? `${number(row.measured_quantity)} ${row.unit ?? ""}`
                  : "–"}
              </td>
              <td className="py-2 pr-3">
                {row.variance_percent != null
                  ? `${Number(row.variance_percent) > 0 ? "+" : ""}${row.variance_percent}%`
                  : row.kind === "client_only"
                    ? "not measured"
                    : row.kind === "measured_only"
                      ? "not billed"
                      : "–"}
              </td>
              <td className="py-2 pr-3 text-xs">{row.qto_items.join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Lines({
  bidId,
  boq,
  onError,
}: {
  bidId: string;
  boq: Boq;
  onError: (caught: unknown) => void;
}) {
  const client = useQueryClient();
  const mark = useMutation({
    mutationFn: async (input: { lineId: string; marker: "provisional" | "lump_sum" }) => {
      const note = window.prompt("Why is there no measured quantity behind this line?");
      if (!note) return;
      const { error } = await api.POST("/bids/{bid_id}/boq/lines/{line_id}/marker", {
        params: { path: { bid_id: bidId, line_id: input.lineId } },
        body: { marker: input.marker, note },
      });
      if (error) throw new Error(apiErrorMessage(error, "Could not mark the line"));
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["boq", bidId] }),
    onError,
  });
  return (
    <div className="space-y-2">
      <h2 className="text-lg font-medium">
        Our BOQ{" "}
        <span className="text-sm font-normal text-muted-foreground">
          version {boq.version}, template {boq.template_key} v{boq.template_version}
        </span>
      </h2>
      <table className="w-full text-sm" aria-label="Our BOQ">
        <thead className="text-left text-xs text-muted-foreground">
          <tr>
            <th className="py-2 pr-3">Item</th>
            <th className="py-2 pr-3">Description</th>
            <th className="py-2 pr-3">Unit</th>
            <th className="py-2 pr-3">Net qty</th>
            <th className="py-2 pr-3">Allowance</th>
            <th className="py-2 pr-3">From</th>
          </tr>
        </thead>
        <tbody>
          {boq.lines.flatMap((line, index) => {
            const heading =
              index === 0 || boq.lines[index - 1]?.section !== line.section ? (
                <tr key={`section-${line.id}`}>
                  <td colSpan={6} className="pt-4 pb-1 font-medium">
                    {line.section}
                  </td>
                </tr>
              ) : null;
            return [
              heading,
              <tr key={line.id} className="border-t align-top">
                <td className="py-2 pr-3">{line.item_no}</td>
                <td className="py-2 pr-3">
                  {line.description}
                  {line.level ? ` (${line.level})` : ""}
                </td>
                <td className="py-2 pr-3">{line.unit}</td>
                <td className="py-2 pr-3">{number(line.quantity)}</td>
                <td className="py-2 pr-3">
                  {line.allowance_percent != null ? `${number(line.allowance_percent)}%` : "–"}
                </td>
                <td className="py-2 pr-3 text-xs">
                  {line.is_provisional || line.is_lump_sum ? (
                    <span title={line.marker_note ?? undefined}>
                      {line.is_provisional ? "provisional sum" : "lump sum"}
                    </span>
                  ) : line.traced ? (
                    line.qto_items.join(", ")
                  ) : (
                    <span className="text-red-700">
                      no QTO trace{" "}
                      <Button
                        variant="outline"
                        onClick={() => mark.mutate({ lineId: line.id, marker: "provisional" })}
                      >
                        Provisional
                      </Button>{" "}
                      <Button
                        variant="outline"
                        onClick={() => mark.mutate({ lineId: line.id, marker: "lump_sum" })}
                      >
                        Lump sum
                      </Button>
                    </span>
                  )}
                </td>
              </tr>,
            ];
          })}
        </tbody>
      </table>
    </div>
  );
}

function ConventionsPanel({
  bidId,
  conventions,
  onError,
}: {
  bidId: string;
  conventions: Conventions;
  onError: (caught: unknown) => void;
}) {
  const client = useQueryClient();
  const save = useMutation({
    mutationFn: async (settings: Record<string, string>) => {
      const { error } = await api.PUT("/bids/{bid_id}/boq/conventions", {
        params: { path: { bid_id: bidId } },
        body: { settings },
      });
      if (error) throw new Error(apiErrorMessage(error, "Could not save the conventions"));
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["boq", bidId, "conventions"] }),
    onError,
  });
  const chosen = Object.fromEntries(conventions.conventions.map((c) => [c.name, c.chosen]));
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-medium">Measurement conventions</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        {conventions.conventions.map((convention) => (
          <label key={convention.name} className="space-y-1 text-sm">
            <span className="block font-medium">{convention.label}</span>
            <select
              className="w-full rounded border px-1"
              value={convention.chosen}
              onChange={(event) =>
                save.mutate({ ...chosen, [convention.name]: event.target.value })
              }
            >
              {convention.options.map((option) => (
                <option key={option.key} value={option.key}>
                  {option.text}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      <pre className="whitespace-pre-wrap rounded-md bg-muted p-3 text-xs">
        {conventions.qualification_text}
      </pre>
    </div>
  );
}
