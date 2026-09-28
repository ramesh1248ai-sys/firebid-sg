import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";

/**
 * Pricing the BOQ from the rate library (FR-CST-01).
 *
 * A price comes only from a library entry: the rules price exact matches, the model proposes
 * an entry where only close ones exist, and an estimator confirms it or chooses another. A
 * line with none says "unpriced" and is left out of the total. Warnings say when a rate has
 * expired, or ends before the tender validity does. Totals exclude GST.
 */

type Pricing = components["schemas"]["PricingOut"];
type Line = components["schemas"]["LinePriceOut"];
type Rate = components["schemas"]["RateOut"];

const STATUS: Record<string, string> = {
  priced: "priced",
  proposed: "proposed: confirm",
  unpriced: "unpriced",
  allowance: "estimator's allowance",
};

function source(rate: Rate | null | undefined): string {
  if (!rate) return "";
  return `${rate.source_type.replaceAll("_", " ")}: ${rate.source_reference}`;
}

export function BoqPricing({
  bidId,
  onError,
}: {
  bidId: string;
  onError: (caught: unknown) => void;
}) {
  const client = useQueryClient();
  const path = { params: { path: { bid_id: bidId } } };
  const pricing = useQuery({
    queryKey: ["boq", bidId, "pricing"],
    queryFn: async (): Promise<Pricing | null> => {
      const { data, response } = await api.GET("/bids/{bid_id}/pricing", path);
      if (response.status === 404) return null;
      if (!data) throw new Error("Could not read the prices");
      return data;
    },
    // The model's proposals arrive from the worker.
    refetchInterval: (query) =>
      query.state.data?.lines.some((line) => line.awaiting_model) ? 5000 : false,
  });
  const done = (data: Pricing) => {
    client.setQueryData(["boq", bidId, "pricing"], data);
    void client.invalidateQueries({ queryKey: ["boq", bidId] });
  };
  const run = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/bids/{bid_id}/pricing/run", path);
      if (error || !data) throw new Error(apiErrorMessage(error, "Could not price the BOQ"));
      return data;
    },
    onSuccess: done,
    onError,
  });
  const confirm = useMutation({
    mutationFn: async (lineId: string) => {
      const { data, error } = await api.POST("/bids/{bid_id}/pricing/lines/{line_id}/confirm", {
        params: { path: { bid_id: bidId, line_id: lineId } },
      });
      if (error || !data) throw new Error(apiErrorMessage(error, "Could not confirm the rate"));
      return data;
    },
    onSuccess: done,
    onError,
  });
  const choose = useMutation({
    mutationFn: async (input: { lineId: string; rateId: string | null }) => {
      const { data, error } = await api.POST("/bids/{bid_id}/pricing/lines/{line_id}/rate", {
        params: { path: { bid_id: bidId, line_id: input.lineId } },
        body: { rate_id: input.rateId, note: null },
      });
      if (error || !data) throw new Error(apiErrorMessage(error, "Could not set the rate"));
      return data;
    },
    onSuccess: done,
    onError,
  });

  const data = pricing.data;
  if (!data) return null;
  const totals = data.totals;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-medium">
          Pricing{" "}
          <span className="text-sm font-normal text-muted-foreground">
            {totals.unpriced} unpriced
            {data.tender_validity_end
              ? `, tender valid to ${data.tender_validity_end}`
              : ", tender validity not set"}
          </span>
        </h2>
        <Button variant="outline" onClick={() => run.mutate()} disabled={run.isPending}>
          Price from the rate library
        </Button>
      </div>
      <table className="w-full text-sm" aria-label="Pricing">
        <thead className="text-left text-xs text-muted-foreground">
          <tr>
            <th className="py-2 pr-3">Item</th>
            <th className="py-2 pr-3">Description</th>
            <th className="py-2 pr-3 text-right">Qty</th>
            <th className="py-2 pr-3 text-right">Rate</th>
            <th className="py-2 pr-3 text-right">Amount</th>
            <th className="py-2 pr-3">Source</th>
            <th className="py-2" />
          </tr>
        </thead>
        <tbody>
          {data.lines.map((line) => (
            <PricingRow
              key={line.line_id}
              bidId={bidId}
              line={line}
              busy={confirm.isPending || choose.isPending}
              onConfirm={() => confirm.mutate(line.line_id)}
              onChoose={(rateId) => choose.mutate({ lineId: line.line_id, rateId })}
            />
          ))}
        </tbody>
        <tfoot className="border-t font-medium">
          {Object.entries(totals.sections).map(([name, amount]) => (
            <tr key={name}>
              <td />
              <td className="py-1 pr-3">Total {name || "(no section)"}</td>
              <td colSpan={2} />
              <td className="py-1 pr-3 text-right">{amount}</td>
              <td colSpan={2} />
            </tr>
          ))}
          <tr>
            <td />
            <td className="py-2 pr-3">Grand total (excluding GST)</td>
            <td colSpan={2} />
            <td className="py-2 pr-3 text-right" aria-label="Grand total">
              {totals.grand}
            </td>
            <td colSpan={2} className="py-2 text-xs font-normal text-muted-foreground">
              {totals.unpriced > 0 ? `${totals.unpriced} unpriced line(s) not included` : ""}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function PricingRow({
  bidId,
  line,
  busy,
  onConfirm,
  onChoose,
}: {
  bidId: string;
  line: Line;
  busy: boolean;
  onConfirm: () => void;
  onChoose: (rateId: string | null) => void;
}) {
  const [choosing, setChoosing] = useState(false);
  const options = useQuery({
    queryKey: ["boq", bidId, "pricing", line.line_id, "candidates"],
    enabled: choosing,
    queryFn: async (): Promise<Rate[]> => {
      const { data } = await api.GET("/bids/{bid_id}/pricing/lines/{line_id}/candidates", {
        params: { path: { bid_id: bidId, line_id: line.line_id } },
      });
      return data ?? [];
    },
  });
  return (
    <tr className="border-t align-top" data-status={line.status}>
      <td className="py-2 pr-3">{line.item_no}</td>
      <td className="py-2 pr-3">
        {line.description}
        {/* An unset tender validity is said once, above the table, not on every line. */}
        {line.warnings
          .filter((warning) => warning.code !== "tender_validity_unknown")
          .map((warning) => (
          <div key={warning.code} className="text-xs text-amber-700">
            {warning.message}
          </div>
        ))}
        {line.superseded && (
          <div className="text-xs text-amber-700">a newer version of this rate exists</div>
        )}
        {line.status === "proposed" && line.reason && (
          <div className="text-xs text-muted-foreground">proposed: {line.reason}</div>
        )}
      </td>
      <td className="py-2 pr-3 text-right">
        {Number(line.quantity)} {line.unit}
      </td>
      <td className="py-2 pr-3 text-right">
        {line.unit_rate ?? (line.proposed ? `(${line.proposed.unit_rate})` : "–")}
      </td>
      <td className="py-2 pr-3 text-right">{line.amount ?? "–"}</td>
      <td className="py-2 pr-3 text-xs">
        <span className={line.status === "unpriced" ? "font-medium text-red-700" : ""}>
          {STATUS[line.status] ?? line.status}
        </span>
        {line.rate && <div>{source(line.rate)}</div>}
        {line.status === "proposed" && <div>{source(line.proposed)}</div>}
        {line.awaiting_model && <div className="text-muted-foreground">asking the model…</div>}
      </td>
      <td className="py-2 whitespace-nowrap">
        {line.status === "proposed" && (
          <Button variant="outline" onClick={onConfirm} disabled={busy}>
            Confirm
          </Button>
        )}{" "}
        {line.status !== "allowance" &&
          (choosing ? (
            <select
              aria-label={`Rate for ${line.description}`}
              className="max-w-64 rounded border px-1 text-xs"
              defaultValue=""
              onChange={(event) => {
                onChoose(event.target.value || null);
                setChoosing(false);
              }}
            >
              <option value="" disabled>
                {options.isLoading ? "Loading…" : "Choose an entry"}
              </option>
              {(options.data ?? []).map((rate) => (
                <option key={rate.id} value={rate.id}>
                  {rate.description} – {rate.unit_rate} ({source(rate)})
                </option>
              ))}
            </select>
          ) : (
            <Button variant="outline" onClick={() => setChoosing(true)} disabled={busy}>
              Choose…
            </Button>
          ))}
      </td>
    </tr>
  );
}
