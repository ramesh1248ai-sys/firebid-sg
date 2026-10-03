import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { accessToken } from "@/auth/oidc";
import { Button } from "@/components/ui/button";

/**
 * Supplier quotations and the cost build-up (FR-CST-02 to 09).
 *
 * A quotation is read from its file and shown field by field beside the line of the file
 * each was read from; a person corrects it, links each line to a BOQ line or an item key,
 * and a senior estimator confirms it, which makes its lines rates at their landed cost in
 * SGD. The build-up shows every cost component as its own line with its basis and source:
 * the priced bill gives the first, an estimator enters the rest under their name, and
 * nothing is filled in for them. Totals are exclusive of GST, with the GST shown apart.
 */

type Quotation = components["schemas"]["QuotationOut"];
type Detail = components["schemas"]["QuotationDetailOut"];
type QuoteLine = components["schemas"]["QuotationLineOut"];
type BuildUp = components["schemas"]["BuildUpOut"];
type BuildUpLine = components["schemas"]["BuildUpLineOut"];
type Comparison = components["schemas"]["ComparisonOut"];

interface Read {
  value?: unknown;
  source?: { text?: string } | null;
  method?: string;
  by?: string;
}

export interface BillLine {
  id: string;
  item_no?: string | null;
  description: string;
}

const FIELDS: [keyof Quotation & string, string][] = [
  ["supplier", "Supplier"],
  ["quote_number", "Quotation number"],
  ["quote_date", "Date"],
  ["valid_until", "Valid until"],
  ["currency", "Currency"],
  ["delivery_terms", "Delivery terms"],
  ["lead_time", "Lead time"],
];

const BASIS: Record<string, string> = {
  calculated: "calculated",
  lump_sum: "lump sum",
  percentage: "percentage",
  "not set": "not set",
};

function readFrom(read: Read | undefined): string {
  if (!read) return "not stated in the file";
  if (read.method === "person") return `entered by ${read.by ?? "a person"}`;
  const words = read.source?.text ?? "";
  return read.method === "model" ? `${words} (read by the model)` : words;
}

export function Quotations({
  bidId,
  lines,
  onError,
}: {
  bidId: string;
  lines: BillLine[];
  onError: (caught: unknown) => void;
}) {
  const client = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const key = ["boq", bidId, "quotations"];
  const quotations = useQuery({
    queryKey: key,
    queryFn: async (): Promise<Quotation[] | null> => {
      const { data } = await api.GET("/bids/{bid_id}/quotations", {
        params: { path: { bid_id: bidId } },
      });
      return data ?? null;
    },
  });
  const upload = useMutation({
    mutationFn: async (chosen: File): Promise<Detail> => {
      const body = new FormData();
      body.append("file", chosen);
      const token = await accessToken();
      const response = await fetch(
        new URL(`/api/bids/${bidId}/quotations`, window.location.origin),
        { method: "POST", headers: token ? { authorization: `Bearer ${token}` } : {}, body },
      );
      const json = await response.json().catch(() => null);
      if (!response.ok) throw new Error(apiErrorMessage(json, "The quotation was not read"));
      return json as Detail;
    },
    onSuccess: (found) => {
      client.setQueryData([...key, found.id], found);
      void client.invalidateQueries({ queryKey: key });
      setOpen(found.id);
    },
    onError,
  });

  const rows = quotations.data;
  if (!rows) return null;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-medium">Supplier quotations</h2>
        <label className="text-sm">
          Add a quotation (PDF, .xlsx or .eml){" "}
          <input
            type="file"
            accept=".pdf,.xlsx,.eml"
            aria-label="Add a quotation"
            className="text-sm"
            disabled={upload.isPending}
            onChange={(event) => {
              const chosen = event.target.files?.[0];
              if (chosen) upload.mutate(chosen);
              event.target.value = "";
            }}
          />
        </label>
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No quotation has been added.</p>
      ) : (
        <table className="w-full text-sm" aria-label="Quotations">
          <thead className="text-left text-xs text-muted-foreground">
            <tr>
              <th className="py-2 pr-3">Supplier</th>
              <th className="py-2 pr-3">Quotation</th>
              <th className="py-2 pr-3">Currency</th>
              <th className="py-2 pr-3">Valid until</th>
              <th className="py-2 pr-3">State</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className="border-t align-top">
                <td className="py-2 pr-3">
                  {row.supplier ?? row.filename}
                  {row.flags.map((flag) => (
                    <div key={flag.code} className="text-xs text-amber-700">
                      {flag.message}
                    </div>
                  ))}
                </td>
                <td className="py-2 pr-3">{row.quote_number ?? "–"}</td>
                <td className="py-2 pr-3">{row.currency ?? "–"}</td>
                <td className="py-2 pr-3">{row.valid_until ?? "–"}</td>
                <td className="py-2 pr-3">
                  {row.state === "extracted" ? "to confirm" : row.state}
                  {row.decided_by ? ` by ${row.decided_by}` : ""}
                </td>
                <td className="py-2">
                  <Button
                    variant="outline"
                    onClick={() => setOpen(open === row.id ? null : row.id)}
                  >
                    {open === row.id ? "Close" : "Check"}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {open && <QuotationCheck bidId={bidId} id={open} lines={lines} onError={onError} />}
    </div>
  );
}

function QuotationCheck({
  bidId,
  id,
  lines,
  onError,
}: {
  bidId: string;
  id: string;
  lines: BillLine[];
  onError: (caught: unknown) => void;
}) {
  const client = useQueryClient();
  const path = { params: { path: { bid_id: bidId, quotation_id: id } } };
  const key = ["boq", bidId, "quotations", id];
  const detail = useQuery({
    queryKey: key,
    queryFn: async (): Promise<Detail | null> => {
      const { data } = await api.GET("/bids/{bid_id}/quotations/{quotation_id}", path);
      return data ?? null;
    },
  });
  const done = (data: Detail) => {
    client.setQueryData(key, data);
    void client.invalidateQueries({ queryKey: ["boq", bidId] });
  };
  const correct = useMutation({
    mutationFn: async (body: components["schemas"]["FieldsIn"]) => {
      const { data, error } = await api.POST("/bids/{bid_id}/quotations/{quotation_id}/fields", {
        ...path,
        body,
      });
      if (error || !data) throw new Error(apiErrorMessage(error, "Could not save the field"));
      return data;
    },
    onSuccess: done,
    onError,
  });
  const link = useMutation({
    mutationFn: async (input: { lineId: string; body: components["schemas"]["LinkIn"] }) => {
      const { data, error } = await api.POST(
        "/bids/{bid_id}/quotations/{quotation_id}/lines/{line_id}/link",
        {
          params: { path: { bid_id: bidId, quotation_id: id, line_id: input.lineId } },
          body: input.body,
        },
      );
      if (error || !data) throw new Error(apiErrorMessage(error, "Could not link the line"));
      return data;
    },
    onSuccess: done,
    onError,
  });
  const act = useMutation({
    mutationFn: async (what: "confirm" | "read-with-model") => {
      const { data, error } = await api.POST(
        what === "confirm"
          ? "/bids/{bid_id}/quotations/{quotation_id}/confirm"
          : "/bids/{bid_id}/quotations/{quotation_id}/read-with-model",
        path,
      );
      if (error || !data) throw new Error(apiErrorMessage(error, "Could not do that"));
      return data;
    },
    onSuccess: done,
    onError,
  });

  const data = detail.data;
  if (!data) return null;
  const fields = data.fields as Record<string, Read>;
  const editable = data.state === "extracted";
  const cited = new Set(
    [...Object.values(fields), ...data.lines.map((line) => ({ source: line.source }) as Read)]
      .map((read) => read.source?.text)
      .filter(Boolean),
  );
  return (
    <section aria-label="Quotation check" className="space-y-3 rounded border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-medium">
          {data.filename}{" "}
          <span className="text-sm font-normal text-muted-foreground">
            read by {data.model ? "the rules and the model" : "the rules"}
          </span>
        </h3>
        {editable && (
          <div className="flex gap-2">
            {data.missing.length > 0 && (
              <Button
                variant="outline"
                onClick={() => act.mutate("read-with-model")}
                disabled={act.isPending}
              >
                Ask the model for what is missing
              </Button>
            )}
            <Button onClick={() => act.mutate("confirm")} disabled={act.isPending}>
              Confirm the quotation
            </Button>
          </div>
        )}
      </div>
      <table className="w-full text-sm" aria-label="Quotation fields">
        <thead className="text-left text-xs text-muted-foreground">
          <tr>
            <th className="py-1 pr-3">Field</th>
            <th className="py-1 pr-3">As read</th>
            <th className="py-1">In the file</th>
          </tr>
        </thead>
        <tbody>
          {FIELDS.map(([name, label]) => {
            const value = (data[name] as string | null) ?? "";
            return (
              <tr key={name} className="border-t align-top">
                <td className="py-1 pr-3">{label}</td>
                <td className="py-1 pr-3">
                  {editable ? (
                    <input
                      aria-label={label}
                      className="w-56 rounded border px-1"
                      defaultValue={value}
                      key={value}
                      onBlur={(event) => {
                        const next = event.target.value.trim();
                        if (next !== value) correct.mutate({ [name]: next || null });
                      }}
                    />
                  ) : (
                    value || "–"
                  )}
                </td>
                <td
                  className={`py-1 text-xs ${
                    fields[name] ? "text-muted-foreground" : "font-medium text-red-700"
                  }`}
                >
                  {readFrom(fields[name])}
                </td>
              </tr>
            );
          })}
          <tr className="border-t align-top">
            <td className="py-1 pr-3">Exclusions</td>
            <td className="py-1 pr-3" colSpan={2}>
              {data.exclusions.length ? data.exclusions.join("; ") : "none stated"}
            </td>
          </tr>
        </tbody>
      </table>
      <table className="w-full text-sm" aria-label="Quotation lines">
        <thead className="text-left text-xs text-muted-foreground">
          <tr>
            <th className="py-1 pr-3">Line</th>
            <th className="py-1 pr-3">Unit</th>
            <th className="py-1 pr-3 text-right">Unit price</th>
            <th className="py-1 pr-3">MOQ</th>
            <th className="py-1 pr-3">Lead time</th>
            <th className="py-1 pr-3">Prices</th>
            <th className="py-1 text-right">Landed, SGD</th>
          </tr>
        </thead>
        <tbody>
          {data.lines.map((line) => (
            <QuoteLineRow
              key={line.id}
              line={line}
              currency={data.currency ?? ""}
              lines={lines}
              editable={editable}
              onLink={(body) => link.mutate({ lineId: line.id, body })}
            />
          ))}
        </tbody>
      </table>
      <details>
        <summary className="cursor-pointer text-sm">
          The file, line by line ({data.source_lines.length})
        </summary>
        <ol className="mt-1 max-h-64 overflow-auto text-xs" aria-label="Source file">
          {data.source_lines.map((line, index) => {
            const words = (line.cells as string[]).join(" | ");
            return (
              <li key={index} className="border-t py-0.5">
                {cited.has(words) ? <mark>{words}</mark> : words}
              </li>
            );
          })}
        </ol>
      </details>
    </section>
  );
}

function QuoteLineRow({
  line,
  currency,
  lines,
  editable,
  onLink,
}: {
  line: QuoteLine;
  currency: string;
  lines: BillLine[];
  editable: boolean;
  onLink: (body: components["schemas"]["LinkIn"]) => void;
}) {
  const landed = line.landed as { unit_cost_sgd?: string; lines?: { label: string; amount: string; basis: string }[] } | null;
  const target = lines.find((candidate) => candidate.id === line.boq_line_id);
  return (
    <tr className="border-t align-top">
      <td className="py-1 pr-3">
        {line.description}
        <div className="text-xs text-muted-foreground">
          {[line.brand, line.model].filter(Boolean).join(" ")}
        </div>
        <div className="text-xs text-muted-foreground">
          in the file: {(line.source as { text?: string }).text ?? ""}
        </div>
      </td>
      <td className="py-1 pr-3">{line.unit ?? "–"}</td>
      <td className="py-1 pr-3 text-right">
        {Number(line.unit_price).toFixed(2)} {currency}
      </td>
      <td className="py-1 pr-3">{line.moq ?? "–"}</td>
      <td className="py-1 pr-3">{line.lead_time ?? "–"}</td>
      <td className="py-1 pr-3 text-xs">
        {editable ? (
          <>
            <select
              aria-label={`BOQ line for ${line.description}`}
              className="max-w-56 rounded border px-1"
              value={line.boq_line_id ?? ""}
              onChange={(event) =>
                onLink({ boq_line_id: event.target.value || null, item_key: null })
              }
            >
              <option value="">no BOQ line</option>
              {lines.map((candidate) => (
                <option key={candidate.id} value={candidate.id}>
                  {candidate.item_no} {candidate.description}
                </option>
              ))}
            </select>
            <input
              aria-label={`Item key for ${line.description}`}
              className="mt-1 block w-56 rounded border px-1"
              placeholder="or an item key: type|dn|material|…"
              defaultValue={line.item_key ?? ""}
              key={line.item_key ?? ""}
              onBlur={(event) => {
                const next = event.target.value.trim();
                if (next !== (line.item_key ?? ""))
                  onLink({ item_key: next || null, boq_line_id: line.boq_line_id ?? null });
              }}
            />
          </>
        ) : (
          <>
            {line.item_label ?? "not linked"}
            {target && <div>{target.item_no} {target.description}</div>}
          </>
        )}
      </td>
      <td className="py-1 text-right">
        {landed?.unit_cost_sgd ?? "–"}
        {(landed?.lines ?? []).map((step) => (
          <div key={step.label} className="text-xs text-muted-foreground">
            {step.label}: {step.amount} ({step.basis})
          </div>
        ))}
      </td>
    </tr>
  );
}

export function CostBuildUp({
  bidId,
  onError,
}: {
  bidId: string;
  onError: (caught: unknown) => void;
}) {
  const client = useQueryClient();
  const path = { params: { path: { bid_id: bidId } } };
  const key = ["boq", bidId, "build-up"];
  const buildUp = useQuery({
    queryKey: key,
    queryFn: async (): Promise<BuildUp | null> => {
      const { data } = await api.GET("/bids/{bid_id}/cost/build-up", path);
      return data ?? null;
    },
  });
  const history = useQuery({
    queryKey: ["boq", bidId, "price-history"],
    queryFn: async (): Promise<Comparison[]> => {
      const { data } = await api.GET("/bids/{bid_id}/cost/history", path);
      return data ?? [];
    },
  });
  const done = (data: BuildUp) => client.setQueryData(key, data);
  const enter = useMutation({
    mutationFn: async (input: { component: string; body: components["schemas"]["EnteredIn"] }) => {
      const { data, error } = await api.PUT("/bids/{bid_id}/cost/build-up/{component}", {
        params: { path: { bid_id: bidId, component: input.component } },
        body: input.body,
      });
      if (error || !data) throw new Error(apiErrorMessage(error, "Could not save the figure"));
      return data;
    },
    onSuccess: done,
    onError,
  });
  const pricedOn = useMutation({
    mutationFn: async (day: string) => {
      const { data, error } = await api.PUT("/bids/{bid_id}/cost/priced-on", {
        ...path,
        body: { priced_on: day },
      });
      if (error || !data) throw new Error(apiErrorMessage(error, "Could not set the date"));
      return data;
    },
    onSuccess: done,
    onError,
  });

  const data = buildUp.data;
  if (!data) return null;
  const outliers = (history.data ?? []).filter((item) => item.outlier);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-medium">
          Cost build-up{" "}
          <span className="text-sm font-normal text-muted-foreground">
            {data.unpriced_lines > 0 ? `${data.unpriced_lines} bill line(s) unpriced` : ""}
            {data.not_set.length > 0 ? ` · ${data.not_set.length} component(s) not set` : ""}
          </span>
        </h2>
        <label className="text-sm">
          Priced on{" "}
          <input
            type="date"
            aria-label="Priced on"
            className="rounded border px-1"
            defaultValue={data.priced_on}
            key={data.priced_on}
            onChange={(event) => event.target.value && pricedOn.mutate(event.target.value)}
          />
          {!data.priced_on_set && (
            <span className="text-xs text-muted-foreground"> (today, until it is set)</span>
          )}
        </label>
      </div>
      <table className="w-full text-sm" aria-label="Cost build-up">
        <thead className="text-left text-xs text-muted-foreground">
          <tr>
            <th className="py-2 pr-3">Component</th>
            <th className="py-2 pr-3">Basis</th>
            <th className="py-2 pr-3">Source</th>
            <th className="py-2 pr-3 text-right">Amount</th>
            <th className="py-2" />
          </tr>
        </thead>
        <tbody>
          {data.lines.map((line) => (
            <BuildUpRow
              key={line.component}
              line={line}
              baseLabels={data.base_labels}
              busy={enter.isPending}
              onEnter={(body) => enter.mutate({ component: line.component, body })}
            />
          ))}
        </tbody>
        <tfoot className="border-t font-medium">
          <tr>
            <td className="py-1 pr-3" colSpan={3}>
              Direct cost
            </td>
            <td className="py-1 pr-3 text-right">{data.direct}</td>
            <td />
          </tr>
          <tr>
            <td className="py-1 pr-3" colSpan={3}>
              Total (excluding GST)
            </td>
            <td className="py-1 pr-3 text-right" aria-label="Total excluding GST">
              {data.total}
            </td>
            <td />
          </tr>
          <tr className="font-normal">
            <td className="py-1 pr-3" colSpan={3}>
              GST at {Number(data.gst_percent)}% (the rate from {data.gst_effective_from})
            </td>
            <td className="py-1 pr-3 text-right" aria-label="GST">
              {data.gst}
            </td>
            <td />
          </tr>
          <tr>
            <td className="py-1 pr-3" colSpan={3}>
              Total (including GST)
            </td>
            <td className="py-1 pr-3 text-right" aria-label="Total including GST">
              {data.total_with_gst}
            </td>
            <td />
          </tr>
        </tfoot>
      </table>
      {outliers.length > 0 && (
        <table className="w-full text-sm" aria-label="Prices unlike their history">
          <caption className="py-1 text-left font-medium text-amber-700">
            Prices unlike their history
          </caption>
          <tbody>
            {outliers.map((item) => (
              <tr key={item.line_id} className="border-t">
                <td className="py-1 pr-3">
                  {item.item_no} {item.description}
                </td>
                <td className="py-1 pr-3 text-right">{Number(item.price).toFixed(2)}</td>
                <td className="py-1 text-xs">
                  {Number(item.deviation_percent) > 0 ? "+" : ""}
                  {Number(item.deviation_percent)}% against a median of{" "}
                  {Number(item.median).toFixed(2)} over {item.history} past price(s),{" "}
                  {Number(item.low).toFixed(2)} to {Number(item.high).toFixed(2)}; tolerance{" "}
                  {Number(item.tolerance_percent)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function BuildUpRow({
  line,
  baseLabels,
  busy,
  onEnter,
}: {
  line: BuildUpLine;
  baseLabels: Record<string, string>;
  busy: boolean;
  onEnter: (body: components["schemas"]["EnteredIn"]) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [basis, setBasis] = useState(line.bases.length ? "percentage" : "lump_sum");
  const [figure, setFigure] = useState("");
  const [base, setBase] = useState(line.bases[0] ?? "");
  return (
    <tr className="border-t align-top" data-basis={line.basis}>
      <td className="py-1 pr-3">{line.label}</td>
      <td className="py-1 pr-3 text-xs">
        {BASIS[line.basis] ?? line.basis}
        <div className="text-muted-foreground">{line.detail}</div>
      </td>
      <td className="py-1 pr-3 text-xs">{line.source}</td>
      <td className="py-1 pr-3 text-right">{line.amount ?? "–"}</td>
      <td className="py-1 whitespace-nowrap text-xs">
        {line.entered &&
          (editing ? (
            <form
              className="flex flex-wrap items-center gap-1"
              onSubmit={(event) => {
                event.preventDefault();
                onEnter(
                  basis === "lump_sum"
                    ? { basis, amount: figure }
                    : { basis, percent: figure, base },
                );
                setEditing(false);
              }}
            >
              {line.bases.length > 0 && (
                <select
                  aria-label={`Basis for ${line.label}`}
                  className="rounded border px-1"
                  value={basis}
                  onChange={(event) => setBasis(event.target.value)}
                >
                  <option value="percentage">% of</option>
                  <option value="lump_sum">lump sum</option>
                </select>
              )}
              {basis === "percentage" && (
                <select
                  aria-label={`Base for ${line.label}`}
                  className="rounded border px-1"
                  value={base}
                  onChange={(event) => setBase(event.target.value)}
                >
                  {line.bases.map((name) => (
                    <option key={name} value={name}>
                      {baseLabels[name] ?? name}
                    </option>
                  ))}
                </select>
              )}
              <input
                aria-label={`Figure for ${line.label}`}
                className="w-24 rounded border px-1"
                inputMode="decimal"
                value={figure}
                onChange={(event) => setFigure(event.target.value)}
              />
              <Button type="submit" variant="outline" disabled={busy || !figure}>
                Save
              </Button>
            </form>
          ) : (
            <Button variant="outline" onClick={() => setEditing(true)} disabled={busy}>
              Enter…
            </Button>
          ))}
      </td>
    </tr>
  );
}
